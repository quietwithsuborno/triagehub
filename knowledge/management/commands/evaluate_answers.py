"""manage.py evaluate_answers: ask the evaluation questions and check the answers.

Each question in knowledge/evaluation_questions.json lists the pages its
answer should come from; an empty list means the documentation has no
answer, so "not found" is right. The command checks what a program can
check (found, and a right page among the sources) and writes everything to
a CSV file, so a person can then check what the answers say.
Example:
    python manage.py evaluate_answers
"""

import csv
import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand

from knowledge.answer import answer_question

# knowledge/management/commands/ -> knowledge/
QUESTIONS_FILE = Path(__file__).resolve().parents[2] / "evaluation_questions.json"
PAUSE_SECONDS = 2.0  # between questions: stays under the AI's free limit


def check(case, answer):
    """Return "pass", or a short reason why the answer is not what we expected."""
    expected_pages = case["pages"]
    if not expected_pages:  # the documentation has no answer
        if answer.found:
            return "answered, but the docs have no answer"
        return "pass"
    if not answer.found:
        return "said not found"
    titles = [source["title"] for source in answer.sources]
    if not any(title in expected_pages for title in titles):
        return "wrong pages: " + ", ".join(titles)
    return "pass"


class Command(BaseCommand):
    help = "Ask the evaluation questions and check the answers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="evaluation_results.csv",
            help="CSV file for the results (default evaluation_results.csv).",
        )

    def handle(self, *args, **options):
        cases = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        rows = []
        for number, case in enumerate(cases, start=1):
            started = time.monotonic()
            try:
                answer = answer_question(case["question"])
            # Any failure is recorded as a failed question; the others go on.
            except Exception as error:
                result = f"error: {type(error).__name__}"
                found, text, sources = "", "", ""
            else:
                result = check(case, answer)
                found, text = answer.found, answer.text
                sources = " | ".join(source["url"] for source in answer.sources)
            rows.append(
                {
                    "number": number,
                    "language": case["language"],
                    "question": case["question"],
                    "expected_pages": " | ".join(case["pages"]) or "(none)",
                    "check": result,
                    "found": found,
                    "answer": text,
                    "sources": sources,
                    "seconds": round(time.monotonic() - started, 1),
                }
            )
            self.stdout.write(f"{number:2}. {result}")
            time.sleep(PAUSE_SECONDS)

        # utf-8-sig: Excel then shows Bangla correctly.
        with open(options["output"], "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        passed = sum(row["check"] == "pass" for row in rows)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {passed} of {len(rows)} passed. Details in {options['output']}"
            )
        )
