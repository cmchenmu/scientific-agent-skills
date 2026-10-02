"""Commands for the deterministic document intake used in Chapter 7."""

from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from app.services.ingestion import download_open_access_articles, ingest_jats_xml
from app.services.knowledge import KnowledgeService
from app.services.local_archive import LocalArchive
from app.services.manuscript_workflow import ManuscriptWorkflow


def connection() -> psycopg.Connection:
    load_dotenv(Path(".env"))
    database_url = os.environ["DATABASE_URL"].replace("+psycopg", "")
    return psycopg.connect(database_url)


def seed_demo(args: argparse.Namespace) -> None:
    organization_id = uuid.UUID(args.organization_id)
    with connection() as conn, conn.cursor() as cursor:
        for name in (
            "student",
            "research-assistant",
            "pi",
            "finance-approver",
            "admin",
        ):
            cursor.execute(
                "INSERT INTO roles (id, name) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (uuid.uuid4(), name),
            )
        cursor.execute(
            "SELECT id FROM projects WHERE organization_id = %s AND name = %s",
            (organization_id, args.project_name),
        )
        row = cursor.fetchone()
        if row:
            project_id = row[0]
        else:
            project_id = uuid.uuid4()
            cursor.execute(
                "INSERT INTO projects (id, organization_id, name, status) VALUES (%s, %s, %s, 'active')",
                (project_id, organization_id, args.project_name),
            )
    print(project_id)


def download(args: argparse.Namespace) -> None:
    manifest = download_open_access_articles(args.query, Path(args.output), args.limit)
    print(manifest)


def ingest(args: argparse.Namespace) -> None:
    with connection() as conn:
        document_id, created = ingest_jats_xml(
            conn,
            Path(args.source),
            uuid.UUID(args.project),
            args.allowed_role,
            args.classification,
        )
    print(f"{document_id} {'created' if created else 'duplicate'}")


def list_documents(args: argparse.Namespace) -> None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, title, version, status, sha256 FROM documents WHERE project_id = %s ORDER BY title",
            (uuid.UUID(args.project),),
        )
        for row in cursor.fetchall():
            print("\t".join(str(value) for value in row))


def local_ingest(args: argparse.Namespace) -> None:
    result = LocalArchive(Path(args.archive)).ingest(
        Path(args.source), args.project, args.allowed_role, args.classification
    )
    print(f"{result.document_id} {result.state} chunks={result.chunk_count}")


def local_documents(args: argparse.Namespace) -> None:
    for document in LocalArchive(Path(args.archive)).list_documents(args.project):
        print("\t".join(str(value) for value in document.values()))


def local_search(args: argparse.Namespace) -> None:
    results = LocalArchive(Path(args.archive)).search(
        args.project, args.role, args.query, args.limit
    )
    for result in results:
        print(result["id"], result["title"], result["text"], sep="\t")


def local_errors(args: argparse.Namespace) -> None:
    for error in LocalArchive(Path(args.archive)).list_errors():
        print("\t".join(str(value) for value in error.values()))


def local_grant_role(args: argparse.Namespace) -> None:
    LocalArchive(Path(args.archive)).grant_role(
        args.user_id, args.project, args.role, args.display_name
    )
    print("granted")


def local_query(args: argparse.Namespace) -> None:
    answer = KnowledgeService(LocalArchive(Path(args.archive))).answer_question(
        args.user_id, args.question, args.project
    )
    print(answer.model_dump_json())


def release_check(args: argparse.Namespace) -> None:
    from app.release_check import run_release_checks

    result = run_release_checks(Path(args.eval_file))
    for name, status in result.items():
        print(f"{name}\t{status}")
    if any(status != "pass" for status in result.values()):
        raise SystemExit(1)


def launch_report(args: argparse.Namespace) -> None:
    from app.launch_report import build_report, render_markdown

    project_root = Path(__file__).resolve().parents[1]
    report = build_report(
        project_root, Path(args.eval_file), run_commands=not args.skip_commands
    )
    Path(args.json_output).write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(args.markdown_output).write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report), end="")
    if not report["ready"]:
        raise SystemExit(1)


def workflow_create(args: argparse.Namespace) -> None:
    workflow = ManuscriptWorkflow(Path(args.root), LocalArchive(Path(args.archive)))
    print(
        workflow.create(
            args.user_id, args.project, args.question, max_steps=args.max_steps
        )
    )


def workflow_plan(args: argparse.Namespace) -> None:
    workflow = ManuscriptWorkflow(Path(args.root), LocalArchive(Path(args.archive)))
    result = workflow.set_search_plan(
        args.workflow_id,
        {
            "query": args.query,
            "sources": ["local"],
            "inclusion": args.inclusion,
            "exclusion": args.exclusion,
        },
    )
    print(result.state)


def workflow_advance(args: argparse.Namespace) -> None:
    workflow = ManuscriptWorkflow(Path(args.root), LocalArchive(Path(args.archive)))
    if args.action == "approve-plan":
        result = workflow.approve_search_plan(args.workflow_id, args.reviewer)
    elif args.action == "retrieve":
        result = workflow.retrieve(args.workflow_id)
    elif args.action == "ingest":
        result = workflow.ingest_retrieved(args.workflow_id)
    elif args.action == "cards":
        result = workflow.create_relevance_cards(args.workflow_id)
    elif args.action == "evidence":
        result = workflow.build_evidence_table(args.workflow_id)
    elif args.action == "draft":
        result = workflow.draft_manuscript(args.workflow_id)
    elif args.action == "complete":
        result = workflow.complete_human_review(
            args.workflow_id, args.reviewer, args.approved
        )
    else:
        raise ValueError("unknown workflow action")
    print(result.state)
    if args.action == "draft":
        print(workflow.draft(args.workflow_id))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    seed = commands.add_parser("seed-demo", help="create the roles and a demo project")
    seed.add_argument("--project-name", default="mouse-neuro-demo")
    seed.add_argument(
        "--organization-id", default="00000000-0000-0000-0000-000000000001"
    )
    seed.set_defaults(handler=seed_demo)

    fetch = commands.add_parser(
        "download-open-access", help="download PMC open-access JATS XML"
    )
    fetch.add_argument(
        "--query",
        required=True,
        help="Europe PMC query expression without source filters",
    )
    fetch.add_argument("--output", default="data/inbox")
    fetch.add_argument("--limit", type=int, default=20)
    fetch.set_defaults(handler=download)

    ingest_parser = commands.add_parser(
        "ingest", help="ingest one authorized JATS XML article"
    )
    ingest_parser.add_argument("--project", required=True, help="project UUID")
    ingest_parser.add_argument("--source", required=True)
    ingest_parser.add_argument("--classification", required=True)
    ingest_parser.add_argument("--allowed-role", action="append", required=True)
    ingest_parser.set_defaults(handler=ingest)

    documents = commands.add_parser("documents", help="list project documents")
    document_commands = documents.add_subparsers(
        dest="documents_command", required=True
    )
    list_parser = document_commands.add_parser("list")
    list_parser.add_argument("--project", required=True)
    list_parser.set_defaults(handler=list_documents)

    local = commands.add_parser("local", help="offline SQLite archive commands")
    local_commands = local.add_subparsers(dest="local_command", required=True)
    local_ingest_parser = local_commands.add_parser("ingest")
    local_ingest_parser.add_argument("--archive", default="data/local-archive")
    local_ingest_parser.add_argument("--project", required=True)
    local_ingest_parser.add_argument("--source", required=True)
    local_ingest_parser.add_argument("--classification", default="internal-research")
    local_ingest_parser.add_argument("--allowed-role", action="append", required=True)
    local_ingest_parser.set_defaults(handler=local_ingest)

    local_list_parser = local_commands.add_parser("list")
    local_list_parser.add_argument("--archive", default="data/local-archive")
    local_list_parser.add_argument("--project", required=True)
    local_list_parser.set_defaults(handler=local_documents)

    local_search_parser = local_commands.add_parser("search")
    local_search_parser.add_argument("--archive", default="data/local-archive")
    local_search_parser.add_argument("--project", required=True)
    local_search_parser.add_argument("--role", action="append", required=True)
    local_search_parser.add_argument("--query", required=True)
    local_search_parser.add_argument("--limit", type=int, default=10)
    local_search_parser.set_defaults(handler=local_search)

    local_errors_parser = local_commands.add_parser("errors")
    local_errors_parser.add_argument("--archive", default="data/local-archive")
    local_errors_parser.set_defaults(handler=local_errors)

    local_grant_parser = local_commands.add_parser("grant-role")
    local_grant_parser.add_argument("--archive", default="data/local-archive")
    local_grant_parser.add_argument("--user-id", required=True)
    local_grant_parser.add_argument("--display-name")
    local_grant_parser.add_argument("--project", required=True)
    local_grant_parser.add_argument("--role", required=True)
    local_grant_parser.set_defaults(handler=local_grant_role)

    local_query_parser = local_commands.add_parser("query")
    local_query_parser.add_argument("--archive", default="data/local-archive")
    local_query_parser.add_argument("--user-id", required=True)
    local_query_parser.add_argument("--project", required=True)
    local_query_parser.add_argument("--question", required=True)
    local_query_parser.set_defaults(handler=local_query)

    release = commands.add_parser(
        "release-check", help="run local Chapter 9 release gates"
    )
    release.add_argument("--eval-file", default="tests/evals/knowledge_cases.jsonl")
    release.set_defaults(handler=release_check)

    launch = commands.add_parser(
        "launch-report", help="run Chapter 13 launch readiness checks"
    )
    launch.add_argument("--eval-file", default="tests/evals/knowledge_cases.jsonl")
    launch.add_argument("--json-output", default="launch-readiness.json")
    launch.add_argument("--markdown-output", default="launch-readiness.md")
    launch.add_argument(
        "--skip-commands",
        action="store_true",
        help="only run deterministic policy checks",
    )
    launch.set_defaults(handler=launch_report)

    workflow = commands.add_parser(
        "workflow", help="run the durable local manuscript workflow"
    )
    workflow_commands = workflow.add_subparsers(dest="workflow_command", required=True)
    create = workflow_commands.add_parser("create")
    create.add_argument("--root", default="data/workflows")
    create.add_argument("--archive", default="data/local-archive")
    create.add_argument("--user-id", required=True)
    create.add_argument("--project", required=True)
    create.add_argument("--question", required=True)
    create.add_argument("--max-steps", type=int, default=10)
    create.set_defaults(handler=workflow_create)
    plan = workflow_commands.add_parser("plan")
    plan.add_argument("--root", default="data/workflows")
    plan.add_argument("--archive", default="data/local-archive")
    plan.add_argument("--workflow-id", required=True)
    plan.add_argument("--query", required=True)
    plan.add_argument("--inclusion", action="append", required=True)
    plan.add_argument("--exclusion", action="append", default=[])
    plan.set_defaults(handler=workflow_plan)
    advance = workflow_commands.add_parser("advance")
    advance.add_argument("--root", default="data/workflows")
    advance.add_argument("--archive", default="data/local-archive")
    advance.add_argument("--workflow-id", required=True)
    advance.add_argument(
        "--action",
        choices=[
            "approve-plan",
            "retrieve",
            "ingest",
            "cards",
            "evidence",
            "draft",
            "complete",
        ],
        required=True,
    )
    advance.add_argument("--reviewer", default="")
    advance.add_argument("--approved", action="store_true")
    advance.set_defaults(handler=workflow_advance)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
