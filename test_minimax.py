import sys
import logging
import os
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.DEBUG)
from runtime.llm_router import get_router
router = get_router()
channels = router.get_channels("semantic")
print("Channels:", channels)
if channels:
    ch = channels[0]
    res = router.call_raw(ch, system_prompt="reply strict JSON", user_payload={"test": 123})
    print("Result:", res)