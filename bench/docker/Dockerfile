# A fresh Debian/Ubuntu machine with jiffy installed from this repo, for the distro
# matrix (bench/docker/run.sh). Docker's base images drop man pages; a real machine
# has them, and jiffy indexes them, so this image puts them back. Ubuntu's minimized
# images also divert /usr/bin/man to a stub that only prints "minimized".
ARG BASE=ubuntu:24.04
FROM ${BASE}

ENV DEBIAN_FRONTEND=noninteractive
RUN rm -f /etc/dpkg/dpkg.cfg.d/excludes \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv man-db manpages less ca-certificates \
      procps psmisc iproute2 iputils-ping dnsutils curl wget git openssh-client rsync \
      file lsof tree jq zip unzip xz-utils bzip2 bc cron at sudo util-linux \
 && apt-get install -y --reinstall --no-install-recommends \
      coreutils findutils grep sed gawk tar gzip util-linux procps dpkg apt passwd \
 && if dpkg-divert --list /usr/bin/man | grep -q man.REAL; then \
      rm -f /usr/bin/man && dpkg-divert --quiet --local --rename --remove /usr/bin/man; fi \
 && mandb -q \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m tester
COPY --chown=tester . /opt/jiffy
USER tester
WORKDIR /opt/jiffy
RUN python3 -m venv ~/venv \
 && ~/venv/bin/pip install -q /opt/jiffy pytest
ENV PATH=/home/tester/venv/bin:$PATH
