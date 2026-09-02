#!/bin/bash
# alertbot 서버 배포. 사용: ./deploy.sh
set -e
HOST=root@165.22.108.193
KEY=~/.ssh/do_key_home
DIR=/opt/alertbot
scp -i $KEY -q *.py README.md run.sh $HOST:$DIR/
ssh -i $KEY $HOST "chmod +x $DIR/run.sh && cd $DIR && md5sum *.py"
echo "--- 로컬 md5 (대조용) ---"
md5sum *.py
