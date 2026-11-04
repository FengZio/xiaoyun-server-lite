#!/bin/bash
# 停止音频分析服务

echo "🛑 停止音频分析Worker..."
systemctl stop audio-analysis-worker
systemctl disable audio-analysis-worker

echo "🧹 清理临时文件..."
rm -rf /tmp/audio_analysis/*

echo "✅ 服务已停止"
