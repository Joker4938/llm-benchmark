import os
import sys
import json
import csv
import time
import threading
from datetime import datetime
from functools import wraps
from io import BytesIO

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_cors import CORS
from openpyxl import Workbook

# Add parent dir to path so we can import llm_benchmark
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_benchmark import run_benchmark

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(REPORTS_DIR, exist_ok=True)

CONFIGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api_configs.json')
configs_lock = threading.Lock()


def _load_configs():
    """Load configs from JSON file, thread-safe. Returns list of configs."""
    with configs_lock:
        if not os.path.exists(CONFIGS_FILE):
            return []
        with open(CONFIGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)


def _save_configs(configs):
    """Save configs to JSON file, thread-safe."""
    with configs_lock:
        with open(CONFIGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(configs, f, ensure_ascii=False, indent=2)


def _generate_config_id():
    """Generate a unique config ID."""
    import uuid
    return uuid.uuid4().hex[:12]

# Single user credentials from env
ADMIN_USERNAME = os.environ.get('BENCH_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('BENCH_PASS', 'admin')

# Global task state (singleton)
task_state = {
    'status': 'idle',  # idle, running, completed, failed
    'completed': 0,
    'total': 0,
    'result': None,
    'results': None,
    'error': None,
    'thread': None,
    'report_files': {}
}
task_lock = threading.Lock()


def create_app():
    app = Flask(__name__, static_folder='../frontend/dist', static_url_path='')
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'change-me-in-production')
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = False

    JWTManager(app)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    @app.errorhandler(Exception)
    def handle_error(e):
        app.logger.error(str(e))
        return jsonify({'message': str(e)}), 500

    # Serve Vue frontend
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve(path):
        if path and os.path.exists(os.path.join(app.static_folder, path)):
            return send_from_directory(app.static_folder, path)
        return send_from_directory(app.static_folder, 'index.html')

    # Auth endpoints
    @app.route('/api/auth/login', methods=['POST'])
    def login():
        data = request.get_json() or {}
        username = data.get('username', '')
        password = data.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            token = create_access_token(identity=username)
            return jsonify({'token': token})
        return jsonify({'message': '用户名或密码错误'}), 401

    @app.route('/api/auth/verify', methods=['GET'])
    @jwt_required()
    def verify():
        return jsonify({'user': get_jwt_identity()})

    # Benchmark endpoints
    @app.route('/api/benchmark/run', methods=['POST'])
    @jwt_required()
    def run_single():
        with task_lock:
            if task_state['status'] == 'running':
                return jsonify({'message': '已有压测任务正在运行，请等待完成'}), 409

            data = request.get_json() or {}
            cfg = {
                'num_requests': int(data.get('numRequests', 50)),
                'concurrency': int(data.get('concurrency', 10)),
                'request_timeout': int(data.get('timeout', 60)),
                'output_tokens': int(data.get('outputTokens', 100)),
                'llm_url': data.get('llm_url', 'http://localhost:8000/v1'),
                'api_key': data.get('api_key', 'default'),
                'model': data.get('model', 'deepseek-r1'),
                'use_long_context': bool(data.get('use_long_context', False)),
            }
            reset_task()
            task_state['status'] = 'running'
            task_state['total'] = cfg['num_requests']

            def progress_cb(completed, total):
                with task_lock:
                    task_state['completed'] = completed
                    task_state['total'] = total

            def target():
                try:
                    import asyncio
                    res = asyncio.run(run_benchmark(
                        num_requests=cfg['num_requests'],
                        concurrency=cfg['concurrency'],
                        request_timeout=cfg['request_timeout'],
                        output_tokens=cfg['output_tokens'],
                        llm_url=cfg['llm_url'],
                        api_key=cfg['api_key'],
                        model=cfg['model'],
                        use_long_context=cfg['use_long_context'],
                        progress_callback=progress_cb
                    ))
                    report_files = save_reports(res, prefix='single_benchmark')
                    with task_lock:
                        task_state['status'] = 'completed'
                        task_state['result'] = res
                        task_state['report_files'] = report_files
                except Exception as e:
                    with task_lock:
                        task_state['status'] = 'failed'
                        task_state['error'] = str(e)

            t = threading.Thread(target=target)
            t.daemon = True
            t.start()
            task_state['thread'] = t

        return jsonify({'task_id': 'current'})

    @app.route('/api/benchmark/run-gradient', methods=['POST'])
    @jwt_required()
    def run_gradient():
        with task_lock:
            if task_state['status'] == 'running':
                return jsonify({'message': '已有压测任务正在运行，请等待完成'}), 409

            data = request.get_json() or {}
            cfg = {
                'llm_url': data.get('llm_url', 'http://localhost:8000/v1'),
                'api_key': data.get('api_key', 'default'),
                'model': data.get('model', 'deepseek-r1'),
                'use_long_context': bool(data.get('use_long_context', False)),
            }
            stages = [
                {'num_requests': 10, 'concurrency': 1, 'output_tokens': 100, 'timeout': 60},
                {'num_requests': 100, 'concurrency': 50, 'output_tokens': 100, 'timeout': 60},
                {'num_requests': 200, 'concurrency': 100, 'output_tokens': 100, 'timeout': 60},
                {'num_requests': 400, 'concurrency': 200, 'output_tokens': 100, 'timeout': 60},
                {'num_requests': 600, 'concurrency': 300, 'output_tokens': 100, 'timeout': 60},
            ]
            total_requests = sum(s['num_requests'] for s in stages)
            reset_task()
            task_state['status'] = 'running'
            task_state['total'] = total_requests

            def progress_cb(completed, total):
                with task_lock:
                    task_state['completed'] = completed
                    task_state['total'] = total

            def target():
                try:
                    import asyncio
                    results_list = []
                    completed_so_far = 0
                    for idx, sc in enumerate(stages):
                        def stage_progress(c, t):
                            progress_cb(completed_so_far + c, total_requests)

                        res = asyncio.run(run_benchmark(
                            num_requests=sc['num_requests'],
                            concurrency=sc['concurrency'],
                            request_timeout=sc['timeout'],
                            output_tokens=sc['output_tokens'],
                            llm_url=cfg['llm_url'],
                            api_key=cfg['api_key'],
                            model=cfg['model'],
                            use_long_context=cfg['use_long_context'],
                            progress_callback=stage_progress
                        ))
                        results_list.append(res)
                        completed_so_far += sc['num_requests']
                        progress_cb(completed_so_far, total_requests)
                        if idx < len(stages) - 1:
                            time.sleep(5)

                    report_files = save_reports(results_list, prefix='gradient_benchmark', is_list=True)
                    with task_lock:
                        task_state['status'] = 'completed'
                        task_state['results'] = results_list
                        task_state['report_files'] = report_files
                except Exception as e:
                    with task_lock:
                        task_state['status'] = 'failed'
                        task_state['error'] = str(e)

            t = threading.Thread(target=target)
            t.daemon = True
            t.start()
            task_state['thread'] = t

        return jsonify({'task_id': 'current'})

    @app.route('/api/task/status', methods=['GET'])
    @jwt_required()
    def task_status():
        with task_lock:
            resp = {
                'status': task_state['status'],
                'completed': task_state['completed'],
                'total': task_state['total']
            }
            if task_state['status'] == 'completed':
                if task_state['results']:
                    resp['results'] = task_state['results']
                elif task_state['result']:
                    resp['result'] = task_state['result']
                resp['report_files'] = task_state['report_files']
            elif task_state['status'] == 'failed':
                resp['error'] = task_state['error']
        return jsonify(resp)

    # Reports endpoints
    @app.route('/api/reports', methods=['GET'])
    @jwt_required()
    def list_reports():
        try:
            files = []
            for f in sorted(os.listdir(REPORTS_DIR)):
                p = os.path.join(REPORTS_DIR, f)
                if os.path.isfile(p):
                    st = os.stat(p)
                    files.append({
                        'filename': f,
                        'size': f"{st.st_size / 1024:.1f} KB",
                        'mtime': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    })
            return jsonify(files)
        except Exception as e:
            return jsonify({'message': str(e)}), 500

    @app.route('/api/reports/download/<path:filename>', methods=['GET'])
    @jwt_required()
    def download_report(filename):
        safe = os.path.basename(filename)
        p = os.path.join(REPORTS_DIR, safe)
        if not os.path.exists(p) or not os.path.isfile(p):
            return jsonify({'message': '文件不存在'}), 404
        return send_from_directory(REPORTS_DIR, safe, as_attachment=True)

    @app.route('/api/reports/<path:filename>', methods=['DELETE'])
    @jwt_required()
    def delete_report(filename):
        safe = os.path.basename(filename)
        p = os.path.join(REPORTS_DIR, safe)
        if not os.path.exists(p):
            return jsonify({'message': '文件不存在'}), 404
        try:
            os.remove(p)
            return jsonify({'message': '删除成功'})
        except Exception as e:
            return jsonify({'message': str(e)}), 500

    # --- API Configuration Preset Endpoints ---

    def _mask_api_key(key):
        """Mask API key: show first 3 and last 4 characters."""
        if not key or len(key) <= 8:
            return key or ''
        return key[:3] + '*' * (len(key) - 7) + key[-4:]

    @app.route('/api/configs', methods=['GET'])
    @jwt_required()
    def list_configs():
        configs = _load_configs()
        # Default first, then by created_at descending
        defaults = [c for c in configs if c.get('is_default')]
        non_defaults = [c for c in configs if not c.get('is_default')]
        non_defaults.sort(key=lambda c: c.get('created_at', ''), reverse=True)
        sorted_configs = defaults + non_defaults
        # Mask API keys
        result = []
        for c in sorted_configs:
            item = {**c, 'api_key': _mask_api_key(c.get('api_key', ''))}
            result.append(item)
        return jsonify(result)

    @app.route('/api/configs', methods=['POST'])
    @jwt_required()
    def create_config():
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        llm_url = (data.get('llm_url') or '').strip()
        api_key = data.get('api_key', '')
        model = (data.get('model') or '').strip()

        if not name:
            return jsonify({'message': '配置名称不能为空'}), 400
        if not llm_url:
            return jsonify({'message': 'API 接口地址不能为空'}), 400
        if not model:
            return jsonify({'message': '模型名称不能为空'}), 400

        configs = _load_configs()
        # Check unique name
        if any(c['name'] == name for c in configs):
            return jsonify({'message': f'配置名称 "{name}" 已存在'}), 409

        new_config = {
            'id': _generate_config_id(),
            'name': name,
            'llm_url': llm_url,
            'api_key': api_key,
            'model': model,
            'is_default': len(configs) == 0,  # first config becomes default
            'created_at': datetime.now().isoformat()
        }
        configs.append(new_config)
        _save_configs(configs)
        return jsonify(new_config), 201

    @app.route('/api/configs/<config_id>', methods=['PUT'])
    @jwt_required()
    def update_config(config_id):
        data = request.get_json() or {}
        configs = _load_configs()
        idx = next((i for i, c in enumerate(configs) if c['id'] == config_id), None)
        if idx is None:
            return jsonify({'message': '配置不存在'}), 404

        config = configs[idx]
        name = data.get('name', config['name']).strip()
        if not name:
            return jsonify({'message': '配置名称不能为空'}), 400

        # Check unique name (excluding self)
        if any(c['name'] == name and c['id'] != config_id for c in configs):
            return jsonify({'message': f'配置名称 "{name}" 已被其他配置使用'}), 409

        llm_url = data.get('llm_url', config['llm_url']).strip()
        if not llm_url:
            return jsonify({'message': 'API 接口地址不能为空'}), 400

        model = data.get('model', config['model']).strip()
        if not model:
            return jsonify({'message': '模型名称不能为空'}), 400

        config['name'] = name
        config['llm_url'] = llm_url
        config['api_key'] = data.get('api_key', config['api_key'])
        config['model'] = model
        _save_configs(configs)
        return jsonify(config)

    @app.route('/api/configs/<config_id>', methods=['DELETE'])
    @jwt_required()
    def delete_config(config_id):
        configs = _load_configs()
        idx = next((i for i, c in enumerate(configs) if c['id'] == config_id), None)
        if idx is None:
            return jsonify({'message': '配置不存在'}), 404

        was_default = configs[idx].get('is_default', False)
        configs.pop(idx)

        # Auto-fallback default
        if was_default and configs:
            configs[0]['is_default'] = True

        _save_configs(configs)
        return jsonify({'message': '删除成功'})

    @app.route('/api/configs/<config_id>/default', methods=['POST'])
    @jwt_required()
    def set_default_config(config_id):
        configs = _load_configs()
        found = False
        for c in configs:
            if c['id'] == config_id:
                c['is_default'] = True
                found = True
            else:
                c['is_default'] = False
        if not found:
            return jsonify({'message': '配置不存在'}), 404
        _save_configs(configs)
        return jsonify({'message': '已设为默认'})

    return app


def reset_task():
    task_state['status'] = 'idle'
    task_state['completed'] = 0
    task_state['total'] = 0
    task_state['result'] = None
    task_state['results'] = None
    task_state['error'] = None
    task_state['thread'] = None
    task_state['report_files'] = {}


def save_reports(data, prefix='benchmark', is_list=False):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filenames = {}

    json_path = os.path.join(REPORTS_DIR, f"{prefix}_{ts}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    filenames['json'] = f"{prefix}_{ts}.json"

    xlsx_path = os.path.join(REPORTS_DIR, f"{prefix}_{ts}.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = '压测结果'
    if is_list:
        ws.append(['并发数', '成功率(%)', 'RPS', 'TPS', '平均延迟(秒)', 'P99延迟(秒)', '首字延迟(秒)'])
        for r in data:
            ws.append([
                r['concurrency'],
                round((r['successful_requests'] / r['total_requests']) * 100, 1),
                round(r['requests_per_second'], 2),
                round(r['tokens_per_second']['average'], 2),
                round(r['latency']['average'], 3),
                round(r['latency']['p99'], 3),
                round(r['time_to_first_token']['average'], 3)
            ])
    else:
        ws.append(['指标分类', '指标名称', '值'])
        rows = [
            ('基本信息', '模型', data.get('model', '-')),
            ('基本信息', '并发数', data['concurrency']),
            ('基本信息', '总请求数', data['total_requests']),
            ('基本信息', '成功请求数', data['successful_requests']),
            ('基本信息', '成功率(%)', round(data['successful_requests'] / data['total_requests'] * 100, 2)),
            ('基本信息', '总耗时(秒)', round(data['total_time'], 3)),
            ('基本信息', '累计输出Token', data['total_output_tokens']),
            ('吞吐性能', 'RPS', round(data['requests_per_second'], 4)),
            ('吞吐性能', 'TPS平均', round(data['tokens_per_second']['average'], 4)),
            ('响应延迟', '平均延迟(秒)', round(data['latency']['average'], 4)),
            ('响应延迟', 'P99延迟(秒)', round(data['latency']['p99'], 4)),
            ('首字延迟', 'TTFT平均(秒)', round(data['time_to_first_token']['average'], 4)),
        ]
        for cat, name, val in rows:
            ws.append([cat, name, val])
    wb.save(xlsx_path)
    filenames['xlsx'] = f"{prefix}_{ts}.xlsx"

    csv_path = os.path.join(REPORTS_DIR, f"{prefix}_{ts}.csv")
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        if is_list:
            writer.writerow(['并发数', '成功率(%)', 'RPS', 'TPS', '平均延迟(秒)', 'P99延迟(秒)', '首字延迟(秒)'])
            for r in data:
                writer.writerow([
                    r['concurrency'],
                    round((r['successful_requests'] / r['total_requests']) * 100, 1),
                    round(r['requests_per_second'], 2),
                    round(r['tokens_per_second']['average'], 2),
                    round(r['latency']['average'], 3),
                    round(r['latency']['p99'], 3),
                    round(r['time_to_first_token']['average'], 3)
                ])
        else:
            writer.writerow(['指标分类', '指标名称', '值'])
            rows = [
                ('基本信息', '模型', data.get('model', '-')),
                ('基本信息', '并发数', data['concurrency']),
                ('基本信息', '总请求数', data['total_requests']),
                ('基本信息', '成功请求数', data['successful_requests']),
                ('基本信息', '成功率(%)', round(data['successful_requests'] / data['total_requests'] * 100, 2)),
                ('基本信息', '总耗时(秒)', round(data['total_time'], 3)),
                ('基本信息', '累计输出Token', data['total_output_tokens']),
                ('吞吐性能', 'RPS', round(data['requests_per_second'], 4)),
                ('吞吐性能', 'TPS平均', round(data['tokens_per_second']['average'], 4)),
                ('响应延迟', '平均延迟(秒)', round(data['latency']['average'], 4)),
                ('响应延迟', 'P99延迟(秒)', round(data['latency']['p99'], 4)),
                ('首字延迟', 'TTFT平均(秒)', round(data['time_to_first_token']['average'], 4)),
            ]
            for cat, name, val in rows:
                writer.writerow([cat, name, val])
    filenames['csv'] = f"{prefix}_{ts}.csv"

    return filenames


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=8080, debug=False)
