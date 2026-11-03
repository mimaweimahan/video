#!/usr/bin/env python3
"""
视频洗片主流程
根据有无口播走两个路径：
- 有口播：转写、改写、配音、匹配/重绘画面（调用付费网关）
- 无口播：直接视频重绘（保留动作和背景声，只换人/衣服）
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

from video_soul_extractor import VideoSpeechExtractor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)


class PayGateway:
    """
    付费网关客户端（示例为 yuangeluyou）
    实际使用请替换成你的网关配置
    """
    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key or os.environ.get('YUAN_GELUYOU_API_KEY', '')
        self.base_url = base_url or os.environ.get('YUAN_GELUYOU_BASE_URL', 'https://yuangeluyou.com/v1')
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({'Authorization': f'Bearer {self.api_key}'})

    def transcribe(self, audio_path: str) -> Tuple[bool, str]:
        """
        语音转文本（调用可灵或其他 ASR 服务）
        :param audio_path: 音频文件路径
        :return: (success, text)
        """
        try:
            # 示例调用，实际应替换为真实 API
            with open(audio_path, 'rb') as f:
                files = {'file': f}
                resp = self.session.post(f'{self.base_url}/transcribe', files=files, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                return True, data.get('text', '')
        except Exception as e:
            log.error('Transcribe failed: %s', e)
            return False, str(e)

    def rewrite_script(self, original_text: str) -> Tuple[bool, str]:
        """
        使用大模型重写语音脚本
        :param original_text: 原文本
        :return: (success, new_text)
        """
        try:
            payload = {
                'model': os.environ.get('MODEL_ALLOCATION', 'gpt-4.1-mini'),
                'prompt': f'请将以下文本改写成更生动、吸引人的版本：\n{original_text}',
                'max_tokens': 500
            }
            resp = self.session.post(f'{self.base_url}/chat/completions', json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            new_text = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            return True, new_text
        except Exception as e:
            log.error('Rewrite script failed: %s', e)
            return False, str(e)

    def text_to_speech(self, text: str, voice: str = 'female-zh-cn') -> Tuple[bool, str]:
        """
        将重写后的文本转为语音
        :param text: 文本
        :param voice: 语音角色
        :return: (success, audio_path)
        """
        try:
            payload = {
                'model': os.environ.get('TTS_MODEL', 'tts-1'),
                'input': text,
                'voice': voice
            }
            resp = self.session.post(f'{self.base_url}/audio/speech', json=payload, timeout=30)
            resp.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            tmp.write(resp.content)
            tmp.close()
            return True, tmp.name
        except Exception as e:
            log.error('TTS failed: %s', e)
            return False, str(e)

    def generate_video_from_script(self, script: str, duration_sec: int = 15) -> Tuple[bool, str]:
        """
        根据脚本生成新画面（调用可灵视频生成或素材库匹配）
        :param script: 脚本
        :param duration_sec: 视频长度（秒）
        :return: (success, video_path)
        """
        # 简化实现，返回一个占位视频
        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        tmp.close()
        # 实际应调用视频生成 API，生成后下载到 tmp.name
        # 这里直接复制一个示例视频
        example_path = Path(__file__).parent / 'assets' / 'example.mp4'
        if example_path.exists():
            shutil.copy(example_path, tmp.name)
        log.warning('Video generation placeholder – should be replaced with real API call')
        return example_path.exists(), tmp.name

    def restyle_video(self, src_video_path: str, pose_ref_image: str = None) -> Tuple[bool, str]:
        """
        视频重绘（保留动作和背景声，只换人/衣服）
        实际调用可灵或 H3 的视频重绘 API
        :param src_video_path: 原视频路径
        :param pose_ref_image: 姿势参考图片（可选）
        :return: (success, video_path)
        """
        try:
            # 示例调用，需要根据实际 API 修改
            with open(src_video_path, 'rb') as f:
                files = {'video': f}
                if pose_ref_image and os.path.exists(pose_ref_image):
                    files['pose_ref'] = open(pose_ref_image, 'rb')
                data = {'mode': 'restyle', 'keep_action': True, 'keep_audio': True}
                resp = self.session.post(f'{self.base_url}/video/restyle', files=files, data=data, timeout=300)
                resp.raise_for_status()
                data = resp.json()
                task_id = data.get('task_id')
                if not task_id:
                    return False, 'No task_id returned'
                # 轮询结果
                for _ in range(30):
                    resp2 = self.session.get(f'{self.base_url}/task/{task_id}', timeout=30)
                    resp2.raise_for_status()
                    info = resp2.json()
                    if info.get('status') == 'completed':
                        result_url = info.get('result_url')
                        if not result_url:
                            return False, 'No result_url'
                        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
                        with requests.get(result_url, stream=True) as r:
                            r.raise_for_status()
                            with open(tmp.name, 'wb') as fw:
                                for chunk in r.iter_content(chunk_size=8192):
                                    fw.write(chunk)
                        return True, tmp.name
                    # 等待 5 秒
                    import time
                    time.sleep(5)
                return False, 'Timeout waiting for restyle result'
        except Exception as e:
            log.error('Restyle failed: %s', e)
            return False, str(e)


class VideoSoulPipeline:
    def __init__(self, gateway=None):
        self.gateway = gateway or PayGateway()
        self.extractor = VideoSpeechExtractor()
        self.output_dir = Path('output')
        self.output_dir.mkdir(exist_ok=True)

    def run(self, video_url: str, **kwargs) -> Dict:
        """
        主流程：
        1. 下载视频到本地
        2. 检测是否有口播
        3. 根据有无口播走不同路径
        4. 返回成片信息
        """
        task_id = str(uuid.uuid4())
        log.info('Task %s started for %s', task_id, video_url)

        # 1. 下载视频
        src_path = self._download_video(video_url)
        if not src_path or not os.path.exists(src_path):
            return {'status': 'error', 'task_id': task_id, 'message': 'Failed to download video'}

        # 2. 语音检测
        has_speech = self._detect_speech(src_path)
        log.info('Speech detection: %s', has_speech)

        # 3. 路径分发
        if has_speech:
            result = self._process_with_speech(src_path, task_id)
        else:
            result = self._process_no_speech(src_path, task_id)

        # 清理临时文件
        try:
            if os.path.exists(src_path):
                os.unlink(src_path)
        except OSError:
            pass

        result['task_id'] = task_id
        result['has_speech'] = has_speech
        return result

    def _download_video(self, url: str) -> Optional[str]:
        """下载视频到临时文件"""
        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        tmp.close()
        try:
            resp = requests.get(url, stream=True, timeout=30)
            resp.raise_for_status()
            with open(tmp.name, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return tmp.name
        except Exception as e:
            log.error('Download failed: %s', e)
            return None

    def _detect_speech(self, video_path: str) -> bool:
        """调用语音检测"""
        return self.extractor.has_speech_from_video(video_path)

    def _process_with_speech(self, video_path: str, task_id: str) -> Dict:
        """
        有口播处理流程：
        转写 → 改写 → 配音 → 匹配或重绘画面
        """
        log.info('Processing video with speech')
        # 1. 提取音频
        audio_path = self._extract_audio(video_path)
        if not audio_path:
            return {'status': 'error', 'message': 'Failed to extract audio'}

        # 2. 语音转文本
        ok, text = self.gateway.transcribe(audio_path)
        if not ok:
            return {'status': 'error', 'message': f'Transcribe failed: {text}'}

        # 3. 重写脚本
        ok, new_text = self.gateway.rewrite_script(text)
        if not ok:
            return {'status': 'error', 'message': f'Rewrite failed: {new_text}'}

        # 4. 生成新语音
        ok, new_audio_path = self.gateway.text_to_speech(new_text)
        if not ok:
            return {'status': 'error', 'message': f'TTS failed: {new_audio_path}'}

        # 5. 生成或重绘视频
        duration = self._get_video_duration(video_path)
        ok, video_path_new = self.gateway.generate_video_from_script(new_text, duration)
        if not ok:
            return {'status': 'error', 'message': f'Video generation failed: {video_path_new}'}

        # 6. 替换音频
        final_path = self._replace_audio(video_path_new, new_audio_path)
        if not final_path:
            return {'status': 'error', 'message': 'Failed to merge audio'}

        # 移动到输出目录
        output_path = self._save_output(final_path, task_id, suffix='_speech')
        return {
            'status': 'success',
            'message': 'Speech processing complete',
            'output_path': output_path,
            'mode': 'full'
        }

    def _process_no_speech(self, video_path: str, task_id: str) -> Dict:
        """
        无口播处理流程：
        直接视频重绘，保留动作和背景声，只换人/衣服
        """
        log.info('Processing no-speech video with restyle')
        # 可选的姿势参考图片（例如用户上传的参考图）
        pose_ref = kwargs.get('pose_ref') if 'kwargs' in locals() else None
        # 调用视频重绘 API
        ok, restyled_path = self.gateway.restyle_video(video_path, pose_ref)
        if not ok:
            return {'status': 'error', 'message': f'Restyle failed: {restyled_path}'}

        output_path = self._save_output(restyled_path, task_id, suffix='_restyle')
        return {
            'status': 'success',
            'message': 'Video restyle complete',
            'output_path': output_path,
            'mode': 'restyle'
        }

    def _extract_audio(self, video_path: str) -> Optional[str]:
        """从视频提取音频"""
        tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        tmp.close()
        cmd = f'ffmpeg -y -i "{video_path}" -vn -ar 16000 -ac 1 -ab 128k -f mp3 "{tmp.name}"'
        try:
            subprocess.run(cmd, shell=True, check=True, capture_output=True)
            return tmp.name
        except subprocess.CalledProcessError:
            return None

    def _get_video_duration(self, video_path: str) -> int:
        """获取视频时长（秒）"""
        cmd = f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{video_path}"'
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return int(float(out.stdout.strip()))
        except:
            return 15

    def _replace_audio(self, video_path: str, audio_path: str) -> Optional[str]:
        """将音频合并到视频（替换原音）"""
        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        tmp.close()
        cmd = f'ffmpeg -y -i "{video_path}" -i "{audio_path}" -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 "{tmp.name}"'
        try:
            subprocess.run(cmd, shell=True, check=True, capture_output=True)
            return tmp.name
        except subprocess.CalledProcessError:
            return None

    def _save_output(self, file_path: str, task_id: str, suffix: str = '') -> str:
        """将最终文件保存到输出目录"""
        ext = os.path.splitext(file_path)[1]
        filename = f'{task_id}{suffix}{ext}'
        dest = self.output_dir / filename
        shutil.move(file_path, dest)
        return str(dest)


if __name__ == '__main__':
    # 示例测试
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else 'share_b3Ylg3C8MvE'
    pipeline = VideoSoulPipeline()
    result = pipeline.run(url)
    print(json.dumps(result, indent=2))