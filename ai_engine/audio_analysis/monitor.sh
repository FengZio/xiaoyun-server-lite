#!/bin/bash
# 监控脚本

echo "=== Audio Analysis Worker 状态 ==="
echo "服务状态:"
systemctl status audio-analysis-worker --no-pager -l

echo -e "\n内存使用:"
free -h

echo -e "\nCPU使用:"
top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print "CPU使用率: " 100 - $1 "%"}'

echo -e "\n磁盘使用:"
df -h /tmp

echo -e "\n进程信息:"
ps aux | grep -E "(python.*worker|redis)" | grep -v grep

echo -e "\n最近日志:"
journalctl -u audio-analysis-worker --no-pager -n 10
