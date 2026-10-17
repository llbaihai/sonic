class BusinessError(Exception):
    def __init__(self, status, code, message, details=None, headers=None):
        self.status, self.code, self.message = status, code, message
        self.details, self.headers = details or [], headers or {}


def not_found():
    return BusinessError(404, "NOT_FOUND", "Resource not found")
