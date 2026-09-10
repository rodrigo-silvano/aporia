#!/usr/bin/env python3
import json

print(json.dumps({
    "protocol": 1,
    "status": "hello",
    "challenge": "0" * 64,
    "core_dumps_disabled": False,
    "memory_lock_status": "unsupported",
}))
