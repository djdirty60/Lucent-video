LOGINFO = LOGDEBUG = LOGWARNING = LOGERROR = 0
def log(*a, **k): pass
def getCondVisibility(*a): return False
def getInfoLabel(*a): return ""
def translatePath(p): return __import__("tempfile").gettempdir() + "/lucent-stub/"
def executebuiltin(*a): pass
def sleep(*a): pass
def getLanguage(*a, **k): return "Dutch"
ISO_639_1 = 0
class Monitor:
    def abortRequested(self): return False
    def waitForAbort(self, t=0): return False
class Player:
    def isPlaying(self): return False
def executeJSONRPC(q):
    import json
    return json.dumps({"result": {"value": "resource.language.nl_nl"}})
