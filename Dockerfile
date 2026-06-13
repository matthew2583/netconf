#
# ***** BEGIN LICENSE BLOCK *****
# 
# Copyright (C) 2023 Olof Hagsand
#
# This file is part of CLIXON
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# ***** END LICENSE BLOCK *****
#

FROM debian:bookworm-slim
LABEL maintainer="local-netconf-lab"

ARG CLIGEN_REPO=https://github.com/clicon/cligen.git
ARG CLIXON_REPO=https://github.com/clicon/clixon.git
ARG CONTROLLER_REPO=https://github.com/clicon/clixon-controller.git
ARG PYAPI_REPO=https://github.com/clicon/clixon-pyapi.git

ARG CLIGEN_REF=master
ARG CLIXON_REF=master
ARG CONTROLLER_REF=main
ARG PYAPI_REF=main

RUN apt update && apt install -y \
    procps emacs-nox git make gcc bison libnghttp2-dev libssl-dev flex \
    python3 python3-pip sudo sshpass && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /clixon
WORKDIR /clixon

RUN git clone --depth 1 --branch ${CLIGEN_REF} ${CLIGEN_REPO} cligen
RUN git clone --depth 1 --branch ${CLIXON_REF} ${CLIXON_REPO} clixon
RUN git clone --depth 1 --branch ${CONTROLLER_REF} ${CONTROLLER_REPO} clixon-controller
RUN git clone --depth 1 --branch ${PYAPI_REF} ${PYAPI_REPO} clixon-pyapi

RUN useradd -m -d /home/clicon clicon

WORKDIR /clixon/cligen
RUN ./configure && make && make install && ldconfig

WORKDIR /clixon/clixon
RUN ./configure && make && make install && ldconfig

WORKDIR /clixon/clixon-controller
RUN ./configure && make && make install && ldconfig

WORKDIR /clixon/clixon-pyapi
RUN python3 setup.py install && cp clixon_server.py /usr/local/bin/

RUN pip3 install -r requirements.txt --break-system-packages

WORKDIR /clixon
RUN pip3 install -r clixon-pyapi/requirements.txt --break-system-packages && \
    cp /usr/local/etc/clixon/controller.xml /usr/local/etc/clixon/clixon.xml && \
    cp /usr/local/etc/clixon/controller.xml /usr/local/etc/clixon.xml

RUN cp /clixon/clixon-controller/docker/ssh-users.yang /usr/local/share/controller/main/ && \
    cp /clixon/clixon-controller/docker/ssh_users.py /usr/local/share/controller/modules/ && \
    cp /clixon/clixon-controller/docker/startsystem.sh /usr/local/bin/

RUN pip3 install saxonche --break-system-packages

RUN mkdir -p /opt/gateway && \
    apt-get update && apt-get install -y wget && \
    wget -q "https://raw.githubusercontent.com/bramstein/xsltjson/master/conf/xml-to-json.xsl" -O /opt/gateway/xml2json.xsl && \
    rm -rf /var/lib/apt/lists/*

COPY gateway/ /usr/local/bin/gateway/
RUN chmod +x /usr/local/bin/gateway/gateway.py
   
ENTRYPOINT ["/usr/local/bin/startsystem.sh"]
