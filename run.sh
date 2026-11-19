#!/bin/bash
exec gunicorn -c gunicorn.conf.py -b 0.0.0.0:$PORT app:app