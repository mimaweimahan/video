#!/usr/bin/env python3
"""
洗片产品 Flask 应用
入口接口：/submit 接受短视频链接，根据是否有语音走完整流程或无口播重绘流程
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime
from urllib.parse import urlparse, unquote

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from pipeline import VideoSoulPipeline

app = Flask(__name__)
pipeline = VideoSoulPipeline()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('video-soul.log')
    ]
)
log = logging.getLogger(__name__)

UPLOAD_FOLDER = 'output'
ALLOWED_EXTENSIONS = {'mp4', 'jpg', 'jpeg', 'png', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/submit', methods=['POST'])
def submit():
    """接收短视频链接，启动洗片流程"""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'status': 'error', 'message': 'Missing video url'}), 400
    url = data['url'].strip()
    try:
        # 启动流程，pipeline.run 会返回结果字典
        result = pipeline.run(url)
        if result.get('status') == 'error':
            log.error('Pipeline error: %s', result.get('message'))
            return jsonify(result), 500
        # 将成片地址改成可直接访问的URL
        output_path = result.get('output_path')
        public_url = None
        if output_path and os.path.exists(output_path):
            filename = os.path.basename(output_path)
            public_url = f'/output/{filename}'
        extra = {'has_speech': result.get('has_speech', False), 'mode': result.get('mode')}
        if not result.get('has_speech'):
            extra['mode'] = 'restyle'
        return jsonify({
            'status': 'success',
            'task_id': result.get('task_id', str(uuid.uuid4())),
            'output_url': public_url,
            'message': result.get('message', 'Processing finished'),
            **extra
        })
    except Exception as e:
        log.exception('Submit processing failed')
        return jsonify({
            'status': 'error',
            'message': f'Internal error: {str(e)}'
        }), 500


@app.route('/output/<path:filename>')
def output_file(filename):
    """提供成片静态文件访问"""
    safe = secure_filename(unquote(filename))
    return send_from_directory(app.config['UPLOAD_FOLDER'], safe)


@app.route('/status/<task_id>')
def status(task_id):
    """查询任务状态"""
    # 简化实现：任务状态会直接在 /submit 返回，这里仅作占位
    return jsonify({
        'status': 'unknown',
        'message': 'Task tracking not implemented; see /submit response directly.'
    })


@app.route('/health')
def health():
    """健康检查"""
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()}), 200


if __name__ == '__main__':
    # 生产使用 gunicorn 启动
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', '8788'))
    debug = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host=host, port=port, debug=debug)