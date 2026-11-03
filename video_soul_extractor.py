#!/usr/bin/env python3
"""
语音检测工具
检测视频是否有口播（人类语音），区分无口播视频
"""

import os
import tempfile

import librosa
import numpy as np
from pydub import AudioSegment
from pydub.silence import detect_nonsilent


class SpeechDetector:
    """
    检测视频是否有语音
    如果整段视频没有人声，认为是无口播视频
    """
    def __init__(self, speech_threshold_db=-40, min_speech_ms=500):
        """
        :param speech_threshold_db: 语音阈值，低于此值认为是非语音（默认为-40dB）
        :param min_speech_ms: 语音片段的起码长度（毫秒）
        """
        self.speech_threshold_db = speech_threshold_db
        self.min_speech_ms = min_speech_ms

    def has_speech(self, audio_path):
        """
        检测音频文件是否含有人声
        :param audio_path: 音频文件路径（支持 mp3, wav, m4a, aac）
        :return: True 如果有语音，False 如果没有
        """
        try:
            # 用 pydub 加载音频
            audio = AudioSegment.from_file(audio_path)
        except Exception as e:
            raise RuntimeError(f"Failed to load audio: {e}")

        # 转为单声道，统一采样率 16kHz（语音检测常用）
        audio = audio.set_channels(1).set_frame_rate(16000)
        # 分帧，每一帧 20ms
        frame_length = 20  # ms
        frames = [audio[i:i+frame_length] for i in range(0, len(audio), frame_length)]
        # 计算每帧的 RMS 能量（dB）
        frame_db = [frame.dBFS for frame in frames]

        # 找出能量超过阈值的连续区间
        speech_segments = []
        in_speech = False
        start = 0
        for i, db in enumerate(frame_db):
            if db > self.speech_threshold_db:
                if not in_speech:
                    start = i
                    in_speech = True
            else:
                if in_speech:
                    duration = (i - start) * frame_length
                    if duration >= self.min_speech_ms:
                        speech_segments.append((start * frame_length, i * frame_length))
                    in_speech = False
        if in_speech:
            duration = (len(frame_db) - start) * frame_length
            if duration >= self.min_speech_ms:
                speech_segments.append((start * frame_length, len(frame_db) * frame_length))

        if speech_segments:
            return True
        return False


class VideoSpeechExtractor:
    """
    从视频中提取音频并检测口播
    """
    def __init__(self, detector=None):
        self.detector = detector or SpeechDetector()

    def has_speech_from_video(self, video_path):
        """
        从视频文件提取音频，检测是否有语音
        :param video_path: 视频文件路径（mp4、mov 等）
        :return: True 如果有语音，False 如果没有
        """
        # 临时文件存放提取的音频
        tmp_dir = tempfile.mkdtemp()
        audio_path = os.path.join(tmp_dir, 'audio.mp3')
        try:
            # 使用 ffmpeg 提取音频（如果使用 moviepy 则依赖 moviepy）
            # 这里用系统 ffmpeg
            cmd = f'ffmpeg -y -i "{video_path}" -vn -ar 16000 -ac 1 -ab 128k -f mp3 "{audio_path}"'
            os.system(cmd + ' > nul 2>&1')
            if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
                raise RuntimeError("Failed to extract audio from video")
            return self.detector.has_speech(audio_path)
        except Exception as e:
            raise RuntimeError(f"Video speech detection failed: {e}")
        finally:
            # 清理临时文件
            try:
                if os.path.exists(audio_path):
                    os.unlink(audio_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def split_speech_segments(self, audio_path, min_silence_ms=500, silence_thresh_db=-40):
        """
        将音频按静音切分成语音段落（用于有口播路径的句子级处理）
        :param audio_path: 音频文件
        :param min_silence_ms: 最小静音长度（毫秒）
        :param silence_thresh_db: 静音阈值（dB）
        :return: 列表，每个元素为 (start_ms, end_ms)
        """
        audio = AudioSegment.from_file(audio_path)
        # 检测非静音区间
        nonsilent = detect_nonsilent(
            audio,
            min_silence_len=min_silence_ms,
            silence_thresh=silence_thresh_db
        )
        # 合并相邻的区间（间隔小于最小静音长度视为同一句）
        merged = []
        for start, end in nonsilent:
            if merged and start - merged[-1][1] < min_silence_ms:
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        return merged


def test():
    """测试语音检测"""
    # 示例：提供一个视频路径（需要实际存在）
    import sys
    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    else:
        return "Usage: python video_speech_extractor.py <video_path>"
    extractor = VideoSpeechExtractor()
    try:
        has = extractor.has_speech_from_video(video_path)
        print(f"Has speech: {has}")
        return has
    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == '__main__':
    test()