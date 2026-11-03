# -*- coding: utf-8 -*-
"""爆款视频链接解析与结构化文案提取。

流水线：
    A. yt-dlp 解析短视频链接，只拉最高音质音轨，ffmpeg 转成 192kbps mp3
    B. 对接大厂 ASR（当前为带句子级时间戳的模拟桩）
    C. 毫秒时间戳统一换算成秒，保留两位小数

用法：
    python3 video_soul_extractor.py "https://www.youtube.com/watch?v=xxxxxxxxxxx"
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List

import yt_dlp


def _ffmpeg_bin() -> str:
    """定位 ffmpeg 可执行文件，优先用系统 PATH 里的。"""
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("系统中未找到 ffmpeg，请先安装并加入 PATH")
    return found


# 抖音解析走公开解析服务，yt-dlp 直接下抖音会被要求 Cookie
DOUYIN_PARSE_BASE = os.environ.get("DOUYIN_PARSE_BASE", "https://demo.douyin.wtf")
DOUYIN_PARSE_KEY = os.environ.get("DOUYIN_PARSE_KEY", "dtk_df6373c8dd2f_b39OkgSfqI2BN8HP-jPxo3k5bvkyxvrg")


def _clean_video_url(text: str) -> str:
    """从抖音分享文案里取出真实链接。

    分享文案形如「4.84 复制此链接，打开Dou音搜索…… https://v.douyin.com/xxxx/」，
    短链后面还粘着说明文字，只保留链接本身。
    """
    match = re.search(r"https?://[^\s]+", text or "")
    if not match:
        raise RuntimeError("没有在文案里找到视频链接")
    url = match.group(0).rstrip(".,;:)]}>，。；：、！!）】\"'")
    # 抖音短链 v.douyin.com 跳一次，拿到最终的视频地址
    if re.search(r"://v\.douyin\.com/", url):
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                url = resp.geturl()
        except urllib.error.HTTPError as exc:
            if exc.headers.get("Location"):
                url = urllib.request.urljoin(url, exc.headers["Location"])
            else:
                raise
    return url


def _download_with_retry(url: str, dest: str, attempts: int = 3) -> None:
    """下载视频文件，CDN 偶发 403 时隔几秒重试。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    last = None
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            return
        except urllib.error.HTTPError as exc:
            last = exc
            time.sleep(4)
    raise RuntimeError(f"视频下载失败：{last}")


def _douyin_video_urls(video_url: str) -> list:
    """用解析服务拿到抖音视频的全部候选下载地址。"""
    body = json.dumps({"url": video_url}).encode()
    data = None
    for _ in range(6):
        req = urllib.request.Request(
            f"{DOUYIN_PARSE_BASE}/api/v1/parse",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": DOUYIN_PARSE_KEY,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError:
            time.sleep(20)
    if data is None:
        raise RuntimeError("抖音解析服务暂时拒绝请求，稍后重试")
    task_id = (data.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"抖音解析没有返回任务：{json.dumps(data, ensure_ascii=False)[:200]}")

    for _ in range(20):
        time.sleep(3)
        poll = urllib.request.Request(
            f"{DOUYIN_PARSE_BASE}/api/v1/tasks/{task_id}",
            headers={
                "X-API-Key": DOUYIN_PARSE_KEY,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            },
        )
        with urllib.request.urlopen(poll, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        payload = result.get("data") or {}
        state = payload.get("state")
        if state == "done":
            media = ((payload.get("data") or {}).get("media") or {}).get("video") or {}
            urls = []
            for candidate in [media.get("url")] + list(media.get("urls") or []):
                if candidate and candidate not in urls:
                    urls.append(candidate)
            if not urls:
                raise RuntimeError("抖音解析完成但没有视频地址")
            return urls
        if state in ("failed", "error"):
            raise RuntimeError(f"抖音解析失败：{json.dumps(payload, ensure_ascii=False)[:200]}")
    raise RuntimeError("抖音解析超时")


def _download_audio(video_url: str, output_dir: str) -> Dict[str, str]:
    """步骤 A：下载原视频，同时抽出 192kbps 的 mp3 供语音识别。

    抖音链接先经解析服务换成直链，其余平台仍用 yt-dlp。
    返回 {"video_id", "audio_file_path", "video_file_path"}。
    """
    os.makedirs(output_dir, exist_ok=True)

    douyin_id = re.search(r"douyin\.com/video/(\d+)", video_url)
    if douyin_id:
        video_id = douyin_id.group(1)
        urls = _douyin_video_urls(video_url)
        video_path = os.path.abspath(os.path.join(output_dir, f"{video_id}.mp4"))
        # CDN 地址时好时坏，逐个试到能下为止
        last = None
        for direct in urls:
            try:
                _download_with_retry(direct, video_path, attempts=1)
                last = None
                break
            except RuntimeError as exc:
                last = exc
        if last:
            raise last
    else:
        video_id, video_path = _ytdlp_download(video_url, output_dir)

    if not os.path.isfile(video_path):
        raise RuntimeError(f"视频下载完成但未找到文件：{video_path}")

    # 抽出音轨并钉死在 192kbps，供语音识别使用
    audio_path = os.path.abspath(os.path.join(output_dir, f"{video_id}.mp3"))
    proc = subprocess.run(
        [_ffmpeg_bin(), "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-b:a", "192k", audio_path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not os.path.isfile(audio_path):
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else "ffmpeg 未返回错误信息"
        raise RuntimeError(f"ffmpeg 转码失败：{tail}")

    return {"video_id": video_id, "audio_file_path": audio_path, "video_file_path": video_path}


def _ytdlp_download(video_url: str, output_dir: str):

    options = {
        "format": "bv*[height<=720]+ba/b[height<=720]/best",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_bin(),
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(video_url, download=True)
        # 播放列表链接只取第一条
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise RuntimeError("播放列表为空，没有可下载的视频")
            info = entries[0]
        video_id = str(info.get("id") or "").strip()
        if not video_id:
            raise RuntimeError("未能从链接中解析出视频唯一 ID")

    video_path = os.path.abspath(os.path.join(output_dir, f"{video_id}.mp4"))
    if not os.path.isfile(video_path):
        raise RuntimeError(f"视频下载完成但未找到文件：{video_path}")
    return video_id, video_path


# 识别模型只加载一次，避免每次请求都重新读权重
_ASR_MODEL = None


def _asr_model():
    global _ASR_MODEL
    if _ASR_MODEL is None:
        from faster_whisper import WhisperModel
        # 服务器无 GPU，small + int8 约占 1.5GB 内存
        _ASR_MODEL = WhisperModel(
            "small", device="cpu", compute_type="int8", cpu_threads=6,
        )
    return _ASR_MODEL


def _call_asr_service(audio_path: str) -> dict:
    """步骤 B：用 faster-whisper 对实际音频做语音识别。

    返回句子级结果，时间戳单位为毫秒，供步骤 C 统一换算。
    模型按音频内容自动判断语言。
    识别不到人声时不报错，返回空句子，交给上游走画面重绘。
    """
    if not os.path.isfile(audio_path):
        raise RuntimeError(f"ASR 输入音频不存在：{audio_path}")

    segments, info = _asr_model().transcribe(
        audio_path,
        vad_filter=True,
        beam_size=5,
    )
    # 只有识别成中文、而且置信度够，才当成口播。
    # 背景音乐经常被硬猜成外语句子，那种情况只重绘画面、不加字幕。
    spoken = info.language == "zh" and info.language_probability >= 0.6
    sentences = []
    if spoken:
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            sentences.append({
                "text": text,
                "start_ms": int(round(segment.start * 1000)),
                "end_ms": int(round(segment.end * 1000)),
            })
    return {"sentences": sentences}


def _normalize_timeline(asr_result: dict) -> List[dict]:
    """步骤 C：毫秒转秒，保留两位小数，整理成时间轴结构。"""
    sentences = asr_result.get("sentences") or []
    if not sentences:
        raise RuntimeError("ASR 没有返回任何句子")

    timeline = []
    for sentence in sentences:
        text = str(sentence.get("text") or "").strip()
        if not text:
            continue
        start_sec = round(float(sentence["start_ms"]) / 1000, 2)
        end_sec = round(float(sentence["end_ms"]) / 1000, 2)
        timeline.append({
            "text": text,
            "start_span": start_sec,
            "end_span": end_sec,
        })

    if not timeline:
        raise RuntimeError("ASR 结果清洗后没有有效句子")
    return timeline


def extract_vocal_and_timeline(video_url: str, output_dir: str = "./download_cache") -> dict:
    """主控函数：解析短视频链接，下载音频，返回结构化口播文案。

    成功返回 status=success 及 video_id、音频路径、全文、句子时间轴。
    解析失败、链接失效或平台封禁时返回 {"status": "error", "message": "..."}，不抛异常。
    """
    try:
        if not video_url or not str(video_url).strip():
            raise RuntimeError("视频链接为空")

        downloaded = _download_audio(_clean_video_url(video_url), output_dir)
        asr_result = _call_asr_service(downloaded["audio_file_path"])
        if not asr_result["sentences"]:
            # 没有口播：保留原视频和背景音，交给画面重绘
            return {
                "status": "success",
                "has_speech": False,
                "video_id": downloaded["video_id"],
                "video_file_path": downloaded.get("video_file_path", ""),
                "audio_file_path": downloaded["audio_file_path"],
                "full_text": "",
                "timeline_structure": [],
            }
        timeline = _normalize_timeline(asr_result)
        full_text = "".join(item["text"] for item in timeline)

        return {
            "status": "success",
            "video_id": downloaded["video_id"],
            "audio_file_path": downloaded["audio_file_path"],
            "video_file_path": downloaded.get("video_file_path", ""),
            "full_text": full_text,
            "timeline_structure": timeline,
            "has_speech": True,
        }
    except Exception as exc:  # 链接失效、反爬封禁、转码失败都走这里
        return {"status": "error", "message": str(exc)}


if __name__ == "__main__":
    # 用法：python3 video_soul_extractor.py "视频链接"
    # 不传参数时用一条公开的 YouTube 短视频做冒烟测试
    test_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    )
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download_cache")
    result = extract_vocal_and_timeline(test_url, cache_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
