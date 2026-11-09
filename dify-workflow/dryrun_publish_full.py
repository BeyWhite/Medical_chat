# -*- coding: utf-8 -*-
"""在「事务内真跑一遍 publish_workflow，然后回滚」——完整验证可发布性且零副作用。

在 api 容器内运行：
    docker exec -i docker-api-1 python - < dryrun_publish_full.py
"""
import traceback

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app_factory import create_app
from extensions.ext_database import db
from libs.datetime_utils import naive_utc_now

APPS = {
    "问答3.0": "37d7e389-1265-456d-a102-94ecaa2f242a",
    "问答4.0": "caecf6d8-5f31-4c24-b426-ea84d4c7bb02",
    "问答5.0": "1d7f324b-9865-4412-9e7f-53aa82decc24",
}


def main():
    _, app = create_app()
    with app.app_context():
        from models import Account, App
        from services.workflow_service import WorkflowService

        svc = WorkflowService()
        factory = sessionmaker(db.engine)
        for name, app_id in APPS.items():
            print("=" * 72)
            print(f"### {name}  ({app_id})")
            session = factory()
            try:
                app_model = session.get(App, app_id)
                account = session.get(Account, "75f7a702-f87c-43c8-9007-346b69bfc318")
                wf = svc.publish_workflow(
                    session=session,
                    app_model=app_model,
                    account=account,
                    marked_name="__dryrun__",
                    marked_comment="dry run, will be rolled back",
                )
                app_model.workflow_id = wf.id
                app_model.updated_at = naive_utc_now()
                session.flush()
                print(f"  [OK] publish_workflow 全程通过：新 version={wf.version} "
                      f"version_number={wf.version_number} id={wf.id}")
                print("       即将回滚（不落盘）…")
                session.rollback()
                print("  [OK] 已回滚")
            except Exception as e:
                session.rollback()
                print(f"  [FAIL] {type(e).__name__}: {e}")
                traceback.print_exc(limit=6)
            finally:
                session.close()
            print()


if __name__ == "__main__":
    main()
