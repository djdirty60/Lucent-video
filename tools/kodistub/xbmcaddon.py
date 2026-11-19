class Addon:
    def __init__(self, *a): pass
    def getSetting(self, k): return ""
    def getSettingBool(self, k): return False
    def getSettingInt(self, k): return 0
    def getAddonInfo(self, k): return {"path": "/home/claude/lucent-video/plugin.video.lucent", "profile": __import__("tempfile").gettempdir() + "/lucent-stub/", "id": "plugin.video.lucent", "version": "1.0.0"}.get(k, "")
    def getLocalizedString(self, n): return ""
    def setSetting(self, *a): pass
