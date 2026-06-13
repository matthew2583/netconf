import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
import saxonche


# xmltojson


def dedup_xmlns(node, parent_ns=None):
    if not isinstance(node, dict):
        return node

    xmlns = node.get("xmlns")
    current_ns = None
    if isinstance(xmlns, dict):
        current_ns = xmlns.get("$")
        if current_ns == parent_ns:
            node = {k: v for k, v in node.items() if k != "xmlns"}
            current_ns = parent_ns

    effective_ns = current_ns or parent_ns
    return {k: dedup_xmlns(v, effective_ns) for k, v in node.items()}


def transform_xml_to_json(xml_text, xslt_file, use_namespaces=True):
    print("Преобразование XML -> JSON...")
    if not os.path.exists(xslt_file):
        print(f"XSLT файл не найден: {xslt_file}")
        return None

    try:
        with saxonche.PySaxonProcessor(license=False) as proc:
            input_node = proc.parse_xml(xml_text=xml_text)
            xslt_proc = proc.new_xslt30_processor()
            executable = xslt_proc.compile_stylesheet(stylesheet_file=xslt_file)
            if use_namespaces:

                executable.set_parameter("use-namespaces", proc.make_boolean_value(True))
            json_text = executable.transform_to_string(xdm_node=input_node)

        if json_text and use_namespaces:
            try:
                data = json.loads(json_text)
                data = dedup_xmlns(data)
                return json.dumps(data, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                pass  

        return json_text
    except Exception as e:
        print(f"Ошибка SaxonC: {e}")
        return None


# jsontoxml


def qname(namespace, name):

    return f"{{{namespace}}}{name}" if namespace else name


def text_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def namespace_from_json(value):
    if not isinstance(value, dict):
        return None

    xmlns = value.get("xmlns")
    if not isinstance(xmlns, dict):
        return None

    default_ns = xmlns.get("$")
    return default_ns if isinstance(default_ns, str) and default_ns else None


def json_elements(name, value, namespace_name=None):
    if isinstance(value, list):
        elements = []
        for item in value:
            elements.extend(json_elements(name, item, namespace_name))
        return elements

    element_namespace = namespace_from_json(value) or namespace_name
    elem = ET.Element(qname(element_namespace, name))
    if isinstance(value, dict):
        text = value.get("$")
        if text is not None:
            elem.text = text_value(text)

        for key, child_value in value.items():
            if key in ("$", "xmlns"):
                continue
            if key.startswith("@"):
                elem.set(key[1:], text_value(child_value))
                continue

            for child in json_elements(key, child_value, element_namespace):
                elem.append(child)
    else:
        elem.text = text_value(value)

    return [elem]


def json_to_device_xml(json_text, device_name):
    print("Преобразование JSON -> XML...")
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"Ошибка JSON: {e}")
        return None

    device = data.get("devices", {}).get("device")
    if isinstance(device, list):
        device = next((item for item in device if item.get("name") == device_name), None)

    if not isinstance(device, dict) or "config" not in device:
        print("Ошибка преобразования JSON -> XML: отсутствует section config")
        return None

    elements = []
    for key, value in device["config"].items():
        if key == "xmlns":
            continue
        ns_uri = namespace_from_json(value)
        elements.extend(json_elements(key, value, ns_uri))

    return "".join(ET.tostring(elem, encoding="unicode") for elem in elements)


# gateway

DEVICES = ["openconfig1", "openconfig2"]
STORAGE_DIR = "/usr/local/var/gateway/configs"
CLIXON_CLI = "clixon_cli -f /usr/local/etc/clixon/clixon.xml"
XSLT_FILE = "/opt/gateway/xml2json.xsl"

CTRL_NS = "http://clicon.org/controller"
NETCONF_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"

ET.register_namespace("ctrl", CTRL_NS)
ET.register_namespace("nc", NETCONF_NS)
ET.register_namespace("rc", "http://clicon.org/restconf")


def json_path(device_name):
    return os.path.join(STORAGE_DIR, f"{device_name}.json")


def strip_comments(xml_text):
    return re.sub(r"<!--.*?-->", "", xml_text, flags=re.DOTALL).strip()


def run_command(command, label, timeout=15, input_text=None):
    try:
        return subprocess.run(
            command,
            shell=True,
            input=input_text,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"Ошибка: таймаут команды {label}")
        return None


def command_ok(result, label):
    if result is None:
        return False
    if result.returncode == 0:
        return True

    output = (result.stdout or "") + (result.stderr or "")
    print(f"Ошибка {label}: returncode={result.returncode}")
    if output.strip():
        print(output.strip())
    return False


def select_device(allow_all=False):
    print("\n")
    print("Доступные устройства:")
    for i, name in enumerate(DEVICES, 1):
        print(f"{i} - {name}")
    if allow_all:
        print("0 - все устройства")

    choice = input("Выберите устройство (номер): ").strip()
    try:
        idx = int(choice)
    except ValueError:
        print("Неверный ввод")
        return None

    if allow_all and idx == 0:
        return DEVICES
    if 1 <= idx <= len(DEVICES):
        return [DEVICES[idx - 1]]

    print("Неверный номер устройства")
    return None


def open_connection(device_name):
    print(f"Открытие соединения с {device_name}...")
    result = run_command(
        f"{CLIXON_CLI} -1 connection open {device_name}",
        "connection open",
        timeout=15,
    )
    return command_ok(result, "открытия соединения")


def get_device_config_xml(device_name):
    print(f"Pull конфигурации с устройства {device_name}...")
    result = run_command(
        f"{CLIXON_CLI} -1 pull {device_name}",
        "pull",
        timeout=15,
    )
    if not command_ok(result, "pull"):
        return None

    result = run_command(
        f"{CLIXON_CLI} -1 show configuration devices device {device_name} config",
        "show configuration",
        timeout=10,
    )
    if not command_ok(result, "получения конфигурации"):
        return None
    return result.stdout


def get_device_entry_xml(device_name):
    result = run_command(
        f"{CLIXON_CLI} -1 show configuration devices device {device_name}",
        "show device",
        timeout=10,
    )
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def save_config(device_name, json_text, xml_text):
    os.makedirs(STORAGE_DIR, exist_ok=True)

    try:
        data = json.loads(json_text)
        with open(json_path(device_name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        with open(json_path(device_name), "w", encoding="utf-8") as f:
            f.write(json_text)

    print(f"Конфигурация сохранена в: {json_path(device_name)}")


def load_json(device_name):
    path = json_path(device_name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def wrap_if_needed(xml_text):
    clean = strip_comments(xml_text)
    return clean if clean.startswith("<config") else f"<config>\n{clean}\n</config>"


def parse_inner_xml(xml_text):
    wrapper = ET.fromstring(f"<root>{xml_text}</root>")
    return list(wrapper)


def build_controller_xml(device_name, device_config_xml):
    device_xml = get_device_entry_xml(device_name)
    if device_xml:
        try:
            root = ET.fromstring(wrap_if_needed(device_xml))
            device = root.find(".//ctrl:device", {"ctrl": CTRL_NS})
            if device is not None:
                old_config = device.find("ctrl:config", {"ctrl": CTRL_NS})
                if old_config is not None:
                    device.remove(old_config)

                new_config = ET.Element(
                    f"{{{CTRL_NS}}}config",
                    {f"{{{NETCONF_NS}}}operation": "replace"},
                )
                for child in parse_inner_xml(device_config_xml):
                    new_config.append(child)
                device.append(new_config)
                return ET.tostring(root, encoding="unicode")
        except Exception as e:
            print(f"Предупреждение: не удалось разобрать XML контроллера ({e})")

    return (
        "<config>"
        f'<devices xmlns="{CTRL_NS}">'
        "<device>"
        f"<name>{device_name}</name>"
        f'<config xmlns:nc="{NETCONF_NS}" nc:operation="replace">'
        f"{device_config_xml}"
        "</config>"
        "</device>"
        "</devices>"
        "</config>"
    )


def edit_candidate(controller_xml):
    try:
        root = ET.fromstring(controller_xml)
        payload = "".join(ET.tostring(child, encoding="unicode") for child in list(root))
    except Exception as e:
        print(f"Ошибка подготовки edit-config: {e}")
        return False

    rpc = f"""
    <?xml version="1.0" encoding="UTF-8"?>
    <rpc xmlns="{NETCONF_NS}" message-id="1">
        <edit-config>
            <target><candidate/></target>
            <config>
                {payload}
            </config>
        </edit-config>
    </rpc>]]>]]>
    """

    result = run_command(
        "clixon_netconf -q0 -f /usr/local/etc/clixon/controller.xml",
        "NETCONF edit-config",
        timeout=30,
        input_text=rpc,
    )
    if result is None:
        return False

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "rpc-error" in output.lower():
        print(f"[netconf edit-config] returncode={result.returncode}")
        if output.strip():
            print(f"[netconf edit-config] output: {output.strip()}")
        return False

    return True


def commit_push():
    for device_name in DEVICES:
        if not open_connection(device_name):
            return False

    result = run_command(
        f"{CLIXON_CLI} -m configure -1 commit push",
        "commit push",
        timeout=30,
    )
    if result is None:
        return False

    output = (result.stdout or "") + (result.stderr or "")
    print(f"[commit push] returncode={result.returncode}")
    if output.strip():
        print(f"[commit push] output: {output.strip()}")

    error_words = ("cli command error", "error", "aborted", "failed", "out-of-sync")
    return result.returncode == 0 and not any(word in output.lower() for word in error_words)


def pull_device(device_name):
    if not open_connection(device_name):
        return "ошибка соединения"

    xml_text = get_device_config_xml(device_name)
    if not xml_text:
        return "ошибка получения XML"

    json_text = transform_xml_to_json(xml_text, XSLT_FILE)
    if not json_text:
        return "ошибка преобразования XML -> JSON"

    save_config(device_name, json_text, xml_text)
    return "успешно"


def show_device(device_name):
    json_text = load_json(device_name)
    print("\n")
    print(f"Конфигурация {device_name}:")
    print("-----------------------------")
    if not json_text:
        print("Файл конфигурации в хранилище пуст или не найден")
    else:
        try:
            print(json.dumps(json.loads(json_text), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(json_text)
    print("-----------------------------")


def push_device(device_name):
    json_text = load_json(device_name)
    if not json_text:
        print(f"Нет данных в хранилище для {device_name}, пропускаем")
        return "пропущено (нет конфига)"

    device_xml = json_to_device_xml(json_text, device_name)
    if not device_xml:
        return "ошибка преобразования JSON -> XML"

    print(f"Применение конфигурации на {device_name}...")
    controller_xml = build_controller_xml(device_name, device_xml)
    if not edit_candidate(controller_xml):
        return "ошибка edit-config"
    if not commit_push():
        return "ошибка commit push"

    print(f"Конфигурация успешно применена на {device_name}")
    return "успешно"


def print_results(title, results):
    print("\n")
    if len(results) == 1:
        status = next(iter(results.values()))
        if status == "успешно":
            print("Операция завершена успешно")
        else:
            print(f"Ошибка: {status}")
        return

    print("-----------------------------")
    print(title)
    for device_name, status in results.items():
        print(f"  {device_name}: {status}")
    print("-----------------------------")


def handle_pull():
    devices = select_device(allow_all=True)
    if devices:
        print_results(
            "Результаты получения конфигурации:",
            {device_name: pull_device(device_name) for device_name in devices},
        )


def handle_show():
    devices = select_device(allow_all=True)
    if devices:
        for device_name in devices:
            show_device(device_name)


def handle_push():
    devices = select_device(allow_all=True)
    if not devices:
        return

    print("\n")
    print("Это перезапишет текущую конфигурацию на выбранных устройствах!")
    if input("Продолжить? (y/n): ").lower() != "y":
        print("Отмена операции")
        return

    print_results(
        "Результаты отправки конфигурации:",
        {device_name: push_device(device_name) for device_name in devices},
    )


def main():
    print(f"Устройства: {', '.join(DEVICES)}")
    print(f"Хранилище: {STORAGE_DIR}")

    actions = {
        "1": handle_pull,
        "2": handle_show,
        "3": handle_push,
    }

    while True:
        print("\n")
        print("Выберите действие:")
        print("1. Получить конфигурацию с устройства (XML -> JSON)")
        print("2. Посмотреть текущую конфигурацию в хранилище")
        print("3. Отправить конфигурацию на устройство (JSON -> XML)")
        print("4. Выйти")
        print("\n")

        choice = input("Введите номер: ").strip()
        if choice == "4":
            print("Выход")
            break

        action = actions.get(choice)
        if action:
            action()
        else:
            print("Неверный выбор")


if __name__ == "__main__":
    main()