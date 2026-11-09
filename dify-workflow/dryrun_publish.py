# -*- coding: utf-8 -*-
"""只读复现「发布前」后端校验（不写库、不发布）。

在 api 容器内运行：
    docker exec -i docker-api-1 python - < dryrun_publish.py
"""
import json
import sys
import traceback
from sqlalchemy import select

from app_factory import create_app
from extensions.ext_database import db

APPS = {
    "问答3.0": "37d7e389-1265-456d-a102-94ecaa2f242a",
    "问答4.0": "caecf6d8-5f31-4c24-b426-ea84d4c7bb02",
    "问答5.0": "1d7f324b-9865-4412-9e7f-53aa82decc24",
}


def main():
    _, app = create_app()
    with app.app_context():
        from models import App
        from models.workflow import Workflow
        from services.workflow_service import WorkflowService
        from core.workflow.llm_environment_variable import (
            validate_llm_environment_model_references,
        )

        svc = WorkflowService()
        for name, app_id in APPS.items():
            print("=" * 70)
            print(f"### {name}  ({app_id})")
            with db.engine.connect() as conn:
                pass
            session = db.session()
            try:
                app_model = session.get(App, app_id)
                draft = session.scalar(
                    select(Workflow).where(
                        Workflow.tenant_id == app_model.tenant_id,
                        Workflow.app_id == app_model.id,
                        Workflow.version == Workflow.VERSION_DRAFT,
                    )
                )
                if draft is None:
                    print("  !! 没有草稿")
                    continue
                graph = draft.graph_dict
                print(f"  草稿 id={draft.id} 节点={len(graph.get('nodes', []))}")

                # 1) 环境变量 / LLM 模型选择器引用
                try:
                    validate_llm_environment_model_references(
                        graph=graph,
                        environment_variables=draft.environment_variables,
                    )
                    print("  [OK] validate_llm_environment_model_references")
                except Exception as e:
                    print(f"  [FAIL] llm env model refs: {type(e).__name__}: {e}")

                # 2) 结构校验
                try:
                    svc.validate_graph_structure(graph=graph)
                    print("  [OK] validate_graph_structure")
                except Exception as e:
                    print(f"  [FAIL] validate_graph_structure: {type(e).__name__}: {e}")

                # 3) 凭证 / 模型可用性（会调插件）
                try:
                    from core.feature_service import SystemFeatureService

                    if SystemFeatureService.is_plugin_manager_enabled():
                        svc._validate_workflow_credentials(draft, session=session)
                        print("  [OK] _validate_workflow_credentials")
                    else:
                        print("  [SKIP] 插件管理器未启用，跳过凭证校验")
                except Exception as e:
                    print(f"  [FAIL] _validate_workflow_credentials: {type(e).__name__}: {e}")
                    traceback.print_exc(limit=3)

                # 4) Agent 节点
                try:
                    from services.agent.workflow_publish_service import (
                        WorkflowAgentPublishService,
                    )

                    WorkflowAgentPublishService.validate_agent_nodes_for_publish(
                        session=session, draft_workflow=draft
                    )
                    print("  [OK] validate_agent_nodes_for_publish")
                except Exception as e:
                    print(f"  [FAIL] validate_agent_nodes: {type(e).__name__}: {e}")

                # 5) 图能否被 graph 引擎解析（模拟一次加载）
                try:
                    from graphon.graph_engine import GraphEngineManager  # noqa: F401

                    print("  [OK] graphon 可用")
                except Exception as e:
                    print(f"  [WARN] graphon import: {e}")

            except Exception as e:
                print(f"  [ERR] {type(e).__name__}: {e}")
            finally:
                session.close()
            print()


if __name__ == "__main__":
    main()
