# -*- coding: utf-8 -*-
import sys
import time
STARTED = time.time()
sys.path.insert(0, __file__.rsplit("/", 1)[0].rsplit("\\", 1)[0] + "/resources/lib")
import router  # noqa: E402

router.STARTED = STARTED
router.run(sys.argv)
