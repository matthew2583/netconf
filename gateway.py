import subprocess
import json
import saxonche
import os
import re
import xml.etree.ElementTree as ET


DEVICES = ["openconfig1", "openconfig2"]
STORAGE_DIR = "/usr/local/var/gateway/configs"
CLIXON_CLI = "clixon_cli -f /usr/local/etc/clixon/clixon.xml"
XSLT_FILE = "/opt/gateway/xml2json.xsl"


# Выбор устройства или всех сразу
def select_device(allow_all=False):
    print("\n")
    print("Доступные устройства:")
    for i, name in enumerate(DEVICES, 1):
        print(f"{i} - {name}")
    if allow_all:
        print(f"0 - все устройства")

    choice = input("Выберите устройство (номер): ").strip()
    try:
        idx = int(choice)
        if allow_all and idx == 0:
            return DEVICES
        if 1 <= idx <= len(DEVICES):
            return [DEVICES[idx - 1]]
        else:
            print("Неверный номер устройства")
            return None
    except ValueError:
        print("Неверный ввод")
        return None


# Открываем соединение с устройством
def open_connection(device_name):
    print(f"Открытие соединения с {device_name}...")
    try:
        cmd = f'{CLIXON_CLI} -1 connection open {device_name}'
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL
        )
        if result.returncode != 0:
            print(f"Ошибка открытия соединения: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("Ошибка: таймаут при открытии соединения")
        return False
    except Exception as e:
        print(f"Ошибка: {e}")
        return False


# Получаем конфигурацию с устройства через pull, затем читаем из контроллера
def get_xml_from_clixon(device_name):
    print(f"Pull конфигурации с устройства {device_name}...")
    try:
        # Сначала pull - забираем актуальный конфиг с устройства в контроллер
        cmd_pull = f'{CLIXON_CLI} -1 pull {device_name}'
        result = subprocess.run(
            cmd_pull,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL
        )
        if result.returncode != 0:
            print(f"Ошибка pull: {result.stderr}")
            return None

        # Затем читаем сохранённый конфиг из контроллера
        cmd_show = f'{CLIXON_CLI} -1 show configuration devices device {device_name} config'
        result = subprocess.run(
            cmd_show,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL
        )
        if result.returncode != 0:
            print(f"Ошибка получения конфигурации: {result.returncode}")
            print(result.stderr)
            return None

        return result.stdout
    except subprocess.TimeoutExpired:
        print("Ошибка: таймаут")
        return None
    except Exception as e:
        print(f"Ошибка: {e}")
        return None


# Получаем текущее описание устройства из контроллера (включая обязательные поля)
def get_device_xml_from_controller(device_name):
    try:
        cmd_show = f'{CLIXON_CLI} -1 show configuration devices device {device_name}'
        result = subprocess.run(
            cmd_show,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except Exception:
        return None


# Преобразуем XML в JSON используя SaxonC и внешний XSLT файл
def transform_xml_to_json_saxon(xml_string):
    print("Преобразование XML -> JSON...")

    if not xml_string:
        return None

    try:
        with saxonche.PySaxonProcessor(license=False) as proc:
            input_node = proc.parse_xml(xml_text=xml_string)
            xslt_proc = proc.new_xslt30_processor()

            if not os.path.exists(XSLT_FILE):
                print(f"XSLT файл не найден: {XSLT_FILE}")
                return None

            executable = xslt_proc.compile_stylesheet(stylesheet_file=XSLT_FILE)
            json_result = executable.transform_to_string(xdm_node=input_node)
            return json_result

    except Exception as e:
        print(f"Ошибка SaxonC: {e}")
        return None


# Сохраняем JSON и оригинальный XML в локальное хранилище
def save_config_locally(json_data, device_name, xml_data=None):
    if not os.path.exists(STORAGE_DIR):
        os.makedirs(STORAGE_DIR)
        print(f"Создана директория: {STORAGE_DIR}")

    filename = os.path.join(STORAGE_DIR, f"{device_name}.json")
    xml_filename = os.path.join(STORAGE_DIR, f"{device_name}.xml")

    try:
        parsed = json.loads(json_data)
        with open(filename, "w") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)
        print(f"Конфигурация сохранена в: {filename}")
    except json.JSONDecodeError:
        with open(filename, "w") as f:
            f.write(json_data)
        print(f"Конфигурация сохранена: {filename}")

    if xml_data:
        with open(xml_filename, "w") as f:
            f.write(xml_data)
        print(f"XML сохранён в: {xml_filename}")

    return filename


# Загружаем JSON из локального хранилища
def load_config_from_storage(device_name):
    filename = os.path.join(STORAGE_DIR, f"{device_name}.json")
    if not os.path.exists(filename):
        return None
    with open(filename, "r") as f:
        return f.read()


# Загружаем оригинальный XML из локального хранилища
def load_xml_from_storage(device_name):
    filename = os.path.join(STORAGE_DIR, f"{device_name}.xml")
    if not os.path.exists(filename):
        return None
    with open(filename, "r") as f:
        return f.read()


# Извлекаем маппинг неймспейсов из сохранённого XML
def extract_namespaces(device_name):
    xml_file = os.path.join(STORAGE_DIR, f"{device_name}.xml")
    if not os.path.exists(xml_file):
        return {}

    try:
        with open(xml_file, "r") as f:
            raw = f.read()
        clean = re.sub(r'<!--.*?-->', '', raw, flags=re.DOTALL).strip()
        root = ET.fromstring(clean)

        ns_map = {}
        for device_el in root.iter():
            local = device_el.tag.split("}")[-1] if "}" in device_el.tag else device_el.tag
            if local == "config":
                for child in device_el:
                    child_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if "}" in child.tag:
                        child_ns = child.tag.split("}")[0][1:]
                        ns_map[child_local] = child_ns
                break
        return ns_map
    except Exception as e:
        print(f"Ошибка извлечения неймспейсов: {e}")
        return {}


# Конвертирует значение Python в строку для XML
def _xml_val(val):
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Обратное преобразование JSON -> XML с сохранением неймспейсов
def json_to_xml_python(json_data, device_name=None):
    print("Преобразование JSON -> XML...")
    try:
        data = json.loads(json_data)
        ns_map = extract_namespaces(device_name) if device_name else {}

        def dict_to_xml(tag, d, namespace=None):
            ns_attr = f' xmlns="{namespace}"' if namespace else ""
            elem_start = f"<{tag}{ns_attr}>"
            elem_end = f"</{tag}>"
            inner = ""

            for key, val in d.items():
                if isinstance(val, dict):
                    inner += dict_to_xml(key, val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            inner += dict_to_xml(key, item)
                        else:
                            inner += f"<{key}>{_xml_val(item)}</{key}>"
                else:
                    inner += f"<{key}>{_xml_val(val)}</{key}>"

            return elem_start + inner + elem_end

        # Структура JSON: {"devices": {"device": {"name": ..., "config": {...}}}}
        config_data = data["devices"]["device"]["config"]
        inner_xml = ""
        for key, val in config_data.items():
            namespace = ns_map.get(key)
            if isinstance(val, dict):
                inner_xml += dict_to_xml(key, val, namespace=namespace)
            else:
                ns_attr = f' xmlns="{namespace}"' if namespace else ""
                inner_xml += f"<{key}{ns_attr}>{_xml_val(val)}</{key}>"

        return inner_xml
    except Exception as e:
        print(f"Ошибка преобразования JSON -> XML: {e}")
        return None


# Собирает полный XML для загрузки в контроллер.
def build_full_xml(xml_string, device_name):
    device_xml = get_device_xml_from_controller(device_name)
    if device_xml:
        clean = re.sub(r'<!--.*?-->', '', device_xml, flags=re.DOTALL).strip()
        try:
            if not clean.startswith("<config"):
                clean = f'<config>\n{clean}\n</config>'

            ET.register_namespace("ctrl", "http://clicon.org/controller")
            ET.register_namespace("rc", "http://clicon.org/restconf")

            root = ET.fromstring(clean)
            ns = {"c": "http://clicon.org/controller"}

            for dev in root.findall(".//c:device", ns):
                name_el = dev.find("c:name", ns)
                if name_el is not None and name_el.text == device_name:
                    old_config = dev.find("c:config", ns)
                    if old_config is not None:
                        dev.remove(old_config)
                    new_config_el = ET.fromstring(
                        '<config xmlns="http://clicon.org/controller" '
                        'xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0" '
                        f'nc:operation="replace">{xml_string}</config>'
                    )
                    dev.append(new_config_el)
                    break

            wrapped_xml = ET.tostring(root, encoding="unicode")
            return wrapped_xml
        except Exception as e:
            print(f"Предупреждение: не удалось разобрать XML из контроллера ({e}), строим минимальную обёртку")

    return f'''<config>
    <devices xmlns="http://clicon.org/controller">
        <device>
                <name>{device_name}</name>
                <config xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0" nc:operation="replace">{xml_string}</config>
            </device>
        </devices>
    </config>'''


# Делает commit/push только для указанного устройства через RESTCONF RPC
def controller_commit_device(device_name, source_ds="candidate"):
    rpc = f'''<?xml version="1.0" encoding="UTF-8"?>
<rpc xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" message-id="1">
  <controller-commit xmlns="http://clicon.org/controller">
    <device>{device_name}</device>
    <push>COMMIT</push>
    <actions>NONE</actions>
    <source>ds:{source_ds}</source>
  </controller-commit>
</rpc>]]>]]>\n'''

    cmd = "clixon_netconf -q0 -f /usr/local/etc/clixon/controller.xml"

    try:
        res = subprocess.run(
            cmd,
            shell=True,
            input=rpc,
            capture_output=True,
            text=True,
            timeout=30
        )
    except subprocess.TimeoutExpired:
        print("Ошибка: таймаут NETCONF commit")
        return False

    output = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0:
        print(f"[netconf commit] returncode={res.returncode}")
        if output.strip():
            print(f"[netconf commit] output: {output.strip()}")
        return False

    if "rpc-error" in output.lower() or "error" in output.lower():
        print(f"[netconf commit] output: {output.strip()}")
        return False

    return True


# Применяет конфигурацию напрямую в running datastore через NETCONF edit-config
def controller_edit_config_candidate(wrapped_xml):
    try:
        root = ET.fromstring(wrapped_xml)
        inner_xml = "".join(
            ET.tostring(child, encoding="unicode") for child in list(root)
        )
    except Exception as e:
        print(f"Ошибка подготовки edit-config: {e}")
        return False

    rpc = f'''<?xml version="1.0" encoding="UTF-8"?>
<rpc xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" message-id="1">
    <edit-config>
        <target><candidate/></target>
    <config>
{inner_xml}
    </config>
  </edit-config>
</rpc>]]>]]>\n'''

    cmd = "clixon_netconf -q0 -f /usr/local/etc/clixon/controller.xml"

    try:
        res = subprocess.run(
            cmd,
            shell=True,
            input=rpc,
            capture_output=True,
            text=True,
            timeout=30
        )
    except subprocess.TimeoutExpired:
        print("Ошибка: таймаут NETCONF edit-config")
        return False

    output = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0:
        print(f"[netconf edit-config] returncode={res.returncode}")
        if output.strip():
            print(f"[netconf edit-config] output: {output.strip()}")
        return False

    if "rpc-error" in output.lower() or "error" in output.lower():
        print(f"[netconf edit-config] output: {output.strip()}")
        return False

    return True


# Загружает конфигурацию на устройство через clixon
def apply_xml_to_clixon(xml_string, device_name):
    print(f"Применение конфигурации на {device_name}...")

    try:
        wrapped_xml = build_full_xml(xml_string, device_name)

        print("--- XML отправляемый в clixon ---")
        print(wrapped_xml[:600])
        print("---------------------------------")

        # Merge keeps mandatory device metadata already present in the controller
        if not controller_edit_config_candidate(wrapped_xml):
            print("Ошибка: edit-config завершился с ошибкой")
            return False

        for dev in DEVICES:
            open_connection(dev)

        cmd_commit = f"{CLIXON_CLI} -m configure -1 commit push"
        res_commit = subprocess.run(
            cmd_commit,
            shell=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30
        )

        commit_output = res_commit.stdout + res_commit.stderr
        print(f"[commit push] returncode={res_commit.returncode}")
        if commit_output.strip():
            print(f"[commit push] output: {commit_output.strip()}")

        error_keywords = ["cli command error", "error", "aborted", "failed", "out-of-sync"]
        has_error = res_commit.returncode != 0 or any(
            kw in commit_output.lower() for kw in error_keywords
        )

        if has_error:
            print("Ошибка применения конфигурации")
            return False

        storage_xml_file = os.path.join(STORAGE_DIR, f"{device_name}.xml")
        with open(storage_xml_file, "w") as f:
            f.write(wrapped_xml)
        print(f"XML-кэш обновлён в: {storage_xml_file}")

        print(f"Конфигурация успешно применена на {device_name}")
        return True

    except subprocess.TimeoutExpired:
        print("Ошибка: таймаут при применении конфигурации")
        return False
    except Exception as e:
        print(f"Ошибка: {e}")
        return False


def main():
    print(f"Устройства: {', '.join(DEVICES)}")
    print(f"Хранилище: {STORAGE_DIR}")

    while True:
        print("\n")
        print("Выберите действие:")
        print("1. Получить конфигурацию с устройства (XML -> JSON)")
        print("2. Посмотреть текущую конфигурацию в хранилище")
        print("3. Отправить конфигурацию на устройство (JSON -> XML)")
        print("4. Выйти")

        print("\n")
        choice = input("Введите номер: ").strip()

        if choice == "1":
            devices = select_device(allow_all=True)
            if not devices:
                continue

            results = {}
            for device in devices:
                if not open_connection(device):
                    results[device] = "ошибка соединения"
                    continue

                xml = get_xml_from_clixon(device)
                if not xml:
                    results[device] = "ошибка получения XML"
                    continue

                json_data = transform_xml_to_json_saxon(xml)
                if not json_data:
                    results[device] = "ошибка преобразования XML -> JSON"
                    continue

                save_config_locally(json_data, device, xml_data=xml)
                results[device] = "успешно"

            print("\n")
            if len(devices) > 1:
                print("-----------------------------")
                print("Результаты получения конфигурации:")
                for device, status in results.items():
                    print(f"  {device}: {status}")
                print("-----------------------------")
            else:
                status = list(results.values())[0]
                if status == "успешно":
                    print("Операция получения конфигурации завершена успешно")
                else:
                    print(f"Ошибка при получении конфигурации: {status}")

        elif choice == "2":
            devices = select_device(allow_all=True)
            if not devices:
                continue

            for device in devices:
                json_data = load_config_from_storage(device)
                print("\n")
                print(f"Конфигурация {device}:")
                print("-----------------------------")
                if json_data:
                    try:
                        parsed = json.loads(json_data)
                        print(json.dumps(parsed, indent=2, ensure_ascii=False))
                    except:
                        print(json_data)
                else:
                    print("Файл конфигурации в хранилище пуст или не найден")
                print("-----------------------------")

        elif choice == "3":
            devices = select_device(allow_all=True)
            if not devices:
                continue

            print("\n")
            print("Это перезапишет текущую конфигурацию на выбранных устройствах!")
            confirm = input("Продолжить? (y/n): ").lower()
            if confirm != 'y':
                print("Отмена операции")
                continue

            results = {}
            for device in devices:
                json_data = load_config_from_storage(device)
                if not json_data:
                    print(f"Нет данных в хранилище для {device}, пропускаем")
                    results[device] = "пропущено (нет конфига)"
                    continue

                if not open_connection(device):
                    results[device] = "ошибка соединения"
                    continue

                xml = json_to_xml_python(json_data, device_name=device)
                if xml and apply_xml_to_clixon(xml, device):
                    results[device] = "успешно"
                else:
                    results[device] = "ошибка применения"

            print("\n")
            if len(devices) > 1:
                print("-----------------------------")
                print("Результаты отправки конфигурации:")
                for device, status in results.items():
                    print(f"  {device}: {status}")
                print("-----------------------------")
            else:
                status = list(results.values())[0]
                if status == "успешно":
                    print("Конфигурация успешно применена")
                else:
                    print(f"Ошибка: {status}")

        elif choice == "4":
            print("Выход")
            break
        else:
            print("Неверный выбор")


if __name__ == "__main__":
    main()