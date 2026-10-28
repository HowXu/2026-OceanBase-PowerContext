"""Generate a plugin-free Dify 1.9.1 Workflow DSL from reviewed rule functions."""
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "station7" / "workflow_logic.py").read_text(encoding="utf-8")


def code(node_id, title, function, variables, outputs):
    arguments = ", ".join(variables)
    return {"id": node_id, "type": "custom", "position": {"x": 0, "y": 0},
            "data": {"type": "code", "title": title, "desc": "", "selected": False,
                     "code_language": "python3",
                     "code": SOURCE + f"\n\ndef main({arguments}):\n    return {function}({arguments})\n",
                     "variables": [{"variable": k, "value_selector": v} for k, v in variables.items()],
                     "outputs": {k: {"type": "string", "children": None} for k in outputs}}}


def http(node_id, title, endpoint, source):
    return {"id": node_id, "type": "custom", "position": {"x": 0, "y": 0},
            "data": {"type": "http-request", "title": title, "desc": "", "selected": False,
                     "method": "post", "url": "{{#env.GATEWAY_URL#}}/internal/" + endpoint,
                     "authorization": {"type": "no-auth", "config": None},
                     "headers": "Content-Type:application/json\nAuthorization:Bearer {{#env.GATEWAY_TOKEN#}}",
                     "params": "", "body": {"type": "raw-text", "data": [
                         {"id": "body", "key": "", "type": "text", "value": "{{#" + source + ".payload#}}"}]},
                     "timeout": {"connect": 10, "read": 60, "write": 10},
                     "ssl_verify": True, "retry_config": {"retry_enabled": True, "max_retries": 2, "retry_interval": 1000}}}


def build():
    nodes = [{"id": "start", "type": "custom", "position": {"x": 0, "y": 0}, "data": {
        "type": "start", "title": "到站登记", "desc": "同一存档口令在新会话复用；每次行动使用新的请求编号。",
        "selected": False, "variables": [
            {"variable": "player_id", "label": "存档口令（8–64 位）", "type": "text-input", "required": True, "max_length": 64},
            {"variable": "action", "label": "你想在车站做什么？", "type": "paragraph", "required": True, "max_length": 500},
            {"variable": "request_id", "label": "本次请求编号（重试时保持不变）", "type": "text-input", "required": True, "max_length": 64}]}},
        code("normalize", "校验并编码输入", "normalize", {
            "player_id": ["start", "player_id"], "action": ["start", "action"], "request_id": ["start", "request_id"]}, ["payload"]),
        http("prepare", "读取 PowerContext 存档", "prepare", "normalize"),
        code("plan", "推进规则并组装 AI 提示词", "plan", {
            "context_body": ["prepare", "body"], "status_code": ["prepare", "status_code"], "payload": ["normalize", "payload"]}, ["payload"]),
        http("narrate", "AI 站长生成旁白", "narrate", "plan"),
        code("finish", "校验旁白响应", "finish", {
            "story_body": ["narrate", "body"], "status_code": ["narrate", "status_code"]}, ["payload"]),
        http("commit", "保存 PowerContext 新修订", "commit", "finish"),
        code("present", "确认存档并输出结果", "present", {
            "commit_body": ["commit", "body"], "status_code": ["commit", "status_code"]}, ["answer", "state", "memory_ref", "narrator"]),
        {"id": "end", "type": "custom", "position": {"x": 0, "y": 0}, "data": {
            "type": "end", "title": "本次到站记录", "desc": "仅在持久化成功后返回结果。", "selected": False,
            "outputs": [{"variable": k, "value_selector": ["present", k]} for k in ["answer", "state", "memory_ref", "narrator"]]}}]
    for i, node in enumerate(nodes):
        node.update(width=250, height=110, position={"x": i * 310, "y": 280},
                    positionAbsolute={"x": i * 310, "y": 280}, sourcePosition="right", targetPosition="left")
    edges = [{"id": a["id"] + "-" + b["id"], "type": "custom", "source": a["id"], "target": b["id"],
              "sourceHandle": "source", "targetHandle": "target",
              "data": {"sourceType": a["data"]["type"], "targetType": b["data"]["type"], "isInIteration": False}}
             for a, b in zip(nodes, nodes[1:])]
    return {"app": {"name": "第七站失物招领", "description": "站长会记得你：用不可能的失物交换线索，在新会话续写自己的结局。",
                    "mode": "workflow", "icon": "🚉", "icon_background": "#D8EBF1", "use_icon_as_answer_icon": False},
            "kind": "app", "version": "0.3.0", "dependencies": [], "workflow": {
                "conversation_variables": [], "environment_variables": [
                    {"id": "b53e25ba-cc54-4ae1-acb0-fd7567eb8495", "name": "GATEWAY_URL", "value": "http://gateway:8787", "value_type": "string", "selector": ["env", "GATEWAY_URL"], "description": "Dify 容器可达的配套服务地址"},
                    {"id": "09852b82-4302-455b-b4d7-7fde13da7344", "name": "GATEWAY_TOKEN", "value": "", "value_type": "secret", "selector": ["env", "GATEWAY_TOKEN"], "description": "与后端 GATEWAY_TOKEN 完全相同"}],
                "features": {"file_upload": {"enabled": False}, "opening_statement": "", "retriever_resource": {"enabled": False},
                             "sensitive_word_avoidance": {"enabled": False}, "speech_to_text": {"enabled": False}, "suggested_questions": [],
                             "suggested_questions_after_answer": {"enabled": False}, "text_to_speech": {"enabled": False}},
                "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.65}}}}


if __name__ == "__main__":
    text = yaml.safe_dump(build(), allow_unicode=True, sort_keys=False, width=100)
    path = ROOT / "station7.yml"
    if "--check" in sys.argv:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            raise SystemExit("DSL is stale. Run python scripts/build_dsl.py")
        print("DSL matches source")
    else:
        path.write_text(text, encoding="utf-8")
        print(path)
