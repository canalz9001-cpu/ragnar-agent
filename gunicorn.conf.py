import os
bind = '0.0.0.0:' + os.environ.get('PORT', '8080')
workers = 1
threads = 4
timeout = 45
accesslog = None  # URLs de verificação podem conter tokens.
errorlog = '-'
def post_worker_init(worker):
    from app import start_worker
    start_worker()
