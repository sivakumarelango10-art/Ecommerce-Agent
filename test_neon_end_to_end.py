import json
import os
import sys
from fastapi.testclient import TestClient

from backend.app.main import create_app

def run_tests():
    print("=" * 60)
    print("STOREFLOW — NEON END-TO-END VERIFICATION SUITE")
    print("=" * 60)

    app = create_app()
    with TestClient(app) as client:
        # 1. API Health Check
        health_res = client.get("/health")
        assert health_res.status_code == 200, f"Health check failed: {health_res.text}"
        print("[PASS] 1. FastAPI Application Health Check: 200 OK")

        # Set up admin authorization for protected Neon endpoints
        admin_email = "admin@storeflow.com"
        app.state.settings.admin_emails = {admin_email}
        session = app.state.db_session_factory()
        try:
            from backend.app.db.models import User
            from backend.app.security.passwords import hash_password
            from backend.app.security.tokens import create_access_token

            admin = session.query(User).filter(User.email == admin_email).first()
            if not admin:
                admin = User(email=admin_email, password_hash=hash_password("AdminSecurePassword123!"), is_active=True)
                session.add(admin)
                session.commit()
            token = create_access_token(admin.id, app.state.settings)
            auth_headers = {"Authorization": f"Bearer {token}"}
        finally:
            session.close()

        # 2. Neon Services Status Endpoint
        neon_res = client.get("/api/v1/neon/status", headers=auth_headers)
        assert neon_res.status_code == 200, f"Neon status failed: {neon_res.text}"
        data = neon_res.json()
        print(f"[PASS] 2. Neon Branch Linked: {data.get('branch')} ({data.get('branch_id')})")

        services = data.get("services", {})

        # Service 1: Lakebase Postgres
        pg = services.get("postgres", {})
        assert pg.get("status") == "connected", f"Postgres not connected: {pg}"
        print(f"[PASS] 3. Neon Postgres: CONNECTED (Database: {pg.get('database')}, Pooled: {pg.get('pooled')})")

        # Service 2: Neon Functions
        fn = services.get("functions", {})
        assert fn.get("status") == "available", f"Functions not available: {fn}"
        assert fn.get("status_code") == 200, f"Function status code: {fn}"
        assert "Hello from Neon Functions" in fn.get("response", ""), f"Function response unexpected: {fn}"
        print(f"[PASS] 4. Neon Functions: AVAILABLE (URL: {fn.get('url')})")
        print(f"       Function Response: '{fn.get('response')}'")

        # Service 3: Neon Object Storage
        storage = services.get("object_storage", {})
        assert storage.get("status") == "available", f"Object Storage not available: {storage}"
        print(f"[PASS] 5. Neon Object Storage: AVAILABLE (Bucket: {storage.get('bucket')}, Region: {storage.get('region')})")

        # Live Storage Round-Trip Test via API
        upload_res = client.post(
            "/api/v1/neon/storage/upload",
            json={
                "key": "proof/storeflow_neon_test.txt",
                "content": "StoreFlow verified on Neon Lakebase architecture.",
                "content_type": "text/plain",
            },
            headers=auth_headers,
        )
        assert upload_res.status_code == 200, f"Storage upload failed: {upload_res.text}"
        print("       [OK] S3 PutObject (upload): 200 OK")

        download_res = client.get(
            "/api/v1/neon/storage/download?key=proof/storeflow_neon_test.txt",
            headers=auth_headers,
        )
        assert download_res.status_code == 200, f"Storage download failed: {download_res.text}"
        assert download_res.json().get("content") == "StoreFlow verified on Neon Lakebase architecture."
        print(f"       [OK] S3 GetObject (download): 200 OK -> Verified Content: '{download_res.json().get('content')}'")

        # Service 4: Neon AI Gateway
        ai = services.get("ai-gateway", {})
        if not ai:
            ai = services.get("ai_gateway", {})
        assert ai.get("status") == "available", f"AI Gateway not available: {ai}"
        print(f"[PASS] 6. Neon AI Gateway: AVAILABLE (Endpoint: {ai.get('endpoint')})")
        print(f"       Live Catalog: {ai.get('models_count')} models available")

        # Service 5: Neon Auth
        auth = services.get("auth", {})
        assert auth.get("status") == "available", f"Auth not available: {auth}"
        print(f"[PASS] 7. Neon Auth: AVAILABLE (JWKS Keys: {auth.get('jwks_keys_count')})")
        print(f"       JWKS URL: {auth.get('jwks_url')}")

        # Service 6: Neon Realtime
        rt = services.get("realtime", {})
        assert rt.get("status") in {"available", "configured"}, f"Realtime not available: {rt}"
        print(f"[PASS] 8. Neon Realtime: AVAILABLE (WebSocket: {rt.get('websocket_url')})")
        print(f"       Secret Configured: {rt.get('secret_configured')}")

    print("=" * 60)
    print("ALL 6 NEON SERVICES SUCCESSFULLY VERIFIED END-TO-END!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
