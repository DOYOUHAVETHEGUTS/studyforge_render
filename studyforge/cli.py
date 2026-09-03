"""
StudyForge CLI.

    python -m studyforge.cli add "Network+" --pdf book.pdf --exam "CompTIA Network+"
    python -m studyforge.cli add "Spanish A1" --file aula1.pdf --type language
    python -m studyforge.cli add "PyRIT" --url https://arxiv.org/abs/....
    python -m studyforge.cli list
    python -m studyforge.cli process <material_id>
    python -m studyforge.cli process <material_id> --failed-only
    python -m studyforge.cli send [<material_id>]
    python -m studyforge.cli diagnose-email
    python -m studyforge.cli serve            # launch the web dashboard
"""
import argparse

from . import config, db, email_delivery, pipeline


def main(argv=None):
    db.init_db()
    p = argparse.ArgumentParser(prog="studyforge")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--pdf")
    a.add_argument("--file")
    a.add_argument("--url")
    a.add_argument("--exam", default="")
    a.add_argument("--objectives", default="")
    a.add_argument("--focus", default="")
    a.add_argument("--type", dest="mtype", default=None,
                   choices=["textbook", "language", "paper", "reference"])

    sub.add_parser("list")

    pr = sub.add_parser("process")
    pr.add_argument("material_id")
    pr.add_argument("--failed-only", action="store_true")
    pr.add_argument("--no-learning", action="store_true")

    se = sub.add_parser("send")
    se.add_argument("material_id", nargs="?")

    sub.add_parser("diagnose-email")

    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)

    ia = sub.add_parser("interview-add")
    ia.add_argument("role")
    ia.add_argument("--company", default="")
    ia.add_argument("--job-file", dest="job_file", help="job posting file (.pdf/.docx/.txt/.md)")
    ia.add_argument("--resume-file", dest="resume_file", help="resume file")
    ia.add_argument("--notes", default="")

    ig = sub.add_parser("interview-generate")
    ig.add_argument("interview_id")

    args = p.parse_args(argv)

    if args.cmd == "add":
        if args.url:
            pipeline.add_material(args.name, args.url, is_url=True, exam_name=args.exam,
                                  objectives=args.objectives, focus=args.focus,
                                  declared_type=args.mtype)
        else:
            src = args.pdf or args.file
            if not src:
                p.error("provide --pdf, --file, or --url")
            pipeline.add_material(args.name, src, exam_name=args.exam,
                                  objectives=args.objectives, focus=args.focus,
                                  declared_type=args.mtype)

    elif args.cmd == "list":
        for m in db.list_materials():
            s = db.stats(m["id"])
            star = "*" if m["active"] else " "
            print(f"{star} {m['id']}  {m['name'][:34]:34}  {m['material_type']:9}  "
                  f"{s['done']}/{s['total']} done  {s['failed']} failed  {s['sent']} sent")

    elif args.cmd == "process":
        fn = pipeline.process_failed_only if args.failed_only else pipeline.process_material
        stats = fn(args.material_id, make_learning=not args.no_learning)
        print(f"Done: {stats}")

    elif args.cmd == "send":
        pipeline.send_next(args.material_id)

    elif args.cmd == "diagnose-email":
        for k, v in email_delivery.diagnose(config.load_settings()).items():
            print(f"{k:12}: {v}")

    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("studyforge.web:app", host=args.host, port=args.port, reload=False)

    elif args.cmd == "interview-add":
        iid = pipeline.add_interview_prep(
            args.role, company=args.company or "",
            job_posting_source=args.job_file, resume_source=args.resume_file,
            notes=args.notes or "")
        print(f"Created interview prep {iid}. Generate it with: interview-generate {iid}")

    elif args.cmd == "interview-generate":
        result = pipeline.generate_interview_prep(args.interview_id)
        print(f"Done: {result}")


if __name__ == "__main__":
    main()
