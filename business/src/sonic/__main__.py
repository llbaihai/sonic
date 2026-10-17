import logging
import uvicorn

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
uvicorn.run('sonic.main:create_app', factory=True, host='127.0.0.1', port=8000,
            access_log=False, limit_concurrency=32, timeout_keep_alive=5)
