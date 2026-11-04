#!/bin/bash
# 启动音频分析服务

echo "🚀 启动音频分析Worker..."

# 检查Redis连接
echo "🔍 检查Redis连接..."
redis-cli -h tag.zrj666.cn -p 6379 -a your_redis_password ping

if [ $? -eq 0 ]; then
    echo "✅ Redis连接正常"
else
    echo "❌ Redis连接失败，请检查网络和配置"
    exit 1
fi

# 启动服务
systemctl daemon-reload
systemctl enable audio-analysis-worker
systemctl start audio-analysis-worker

echo "✅ 服务已启动"
echo "📊 查看状态: /opt/xiaoyun/xiaoyun-server-lite/ai_engine/audio_analysis/monitor.sh"
echo "📝 查看日志: journalctl -u audio-analysis-worker -f"
