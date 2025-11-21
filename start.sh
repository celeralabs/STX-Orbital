#!/bin/bash
gunicorn main:app --bind 0.0.0.0:$PORT --workers 2
```

Update your Procfile:
```
web: bash start.sh