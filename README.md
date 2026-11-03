# 洗片产品 API 流程

## 概述
这是一个基于 Flask 的短视频洗片服务，支持无口播视频的重绘路径。产品通过粘贴短视频链接，提取视频，判断是否有语音，如果有则走完整流程（转录、改写、配音、匹配或重绘），如果没有则直接视频重绘（保留动作和背景声，只换人/衣服）。

## 流程
1. 用户输入视频链接
2. 服务提取视频，检测是否有语音
   - 无口播：转视频重绘
   - 有口播：转录 → 改写 → 语音合成 → 匹配画面或重绘
3. 返回成片

## 无口播路径
- 无需转录、改写、重新配音
- 保留原始动作、相机运动和背景音乐
- 通过视频重绘模型（MiniMax H3）改换人物和服装
- 输出相同时长、相同帧数、仅换人的新视频

## 有口播路径
- 先转写语音成字幕
- 使用大模型重写脚本
- 用语音合成替换原声
- 匹配生成新画面或重绘原画面
- 重新合成视频

## 环境变量（示例）
```
YUAN_GELUYOU_API_KEY=sk-xxx
YUAN_GELUYOU_BASE_URL=https://yuangeluyou.com/v1
MODEL_ALLOCATION="gpt-4.1-mini,gpt-4.1,o4-mini"
VECTOR_EMBEDDING="text-embedding-3-small"
XINGHUO_AI_ACCESS_KEY=
XINGHUO_AI_SECRET_KEY=
KLING_API_KEY=
KLING_BASE_URL=https://api.klingai.com/v1
```

## 启动服务
```shell
pip install -r requirements.txt
python app.py
```
服务监听 0.0.0.0:8788

## 测试
```shell
curl -X POST http://localhost:8788/submit -H "Content-Type: application/json" -d '{"url":"share_b3Ylg3C8MvE"}'
```

## 部署
- 在 VPS 上运行 systemd 服务 `video-soul`
- 配置防火墙开放 8788 端口
- 配合视频提取、重绘等外部服务使用

## 依赖
- Flask
- requests
- 语音检测（pydub, vosk）
- 视频处理（moviepy, opencv-python）
- 第三方 API 客户端