from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import FrontendFiles


def test_html_entry_and_history_fallback_cannot_stick_to_an_old_build(tmp_path):
    (tmp_path/'index.html').write_text('<html>current frontend</html>')
    (tmp_path/'current-hash.js').write_text('/* bundled module */')
    app=FastAPI()
    app.mount('/',FrontendFiles(directory=tmp_path,html=True))
    with TestClient(app) as client:
        for path in ('/','/index.html','/game-rules/index'):
            response=client.get(path)
            assert response.status_code==200
            assert 'no-store' in response.headers.get('cache-control','')
            assert response.text=='<html>current frontend</html>'
        assert client.get('/missing-old-hash.js').status_code==404
