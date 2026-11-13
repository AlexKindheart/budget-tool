"""Set a password (the first time) and unlock, as a person would, before a test uses the database."""
import app as A
import security

PW = "test password 123"
KEY = security.create(PW)[0] if not security.is_set_up() else security.unlock(PW)
with A.app.test_request_context():
    A.finish_unlock(KEY)
