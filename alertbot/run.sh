#!/bin/bash
# 종가베팅 브리핑 실행 래퍼. 인자: 슬롯(07|1430|19) [추가옵션...]
cd /opt/alertbot || exit 1
export PYTHONIOENCODING=utf-8
SLOT="$1"; shift
echo "===== $(TZ=Asia/Seoul date '+%F %T KST') slot=$SLOT ====="
/usr/bin/python3 cli.py --slot "$SLOT" "$@"
