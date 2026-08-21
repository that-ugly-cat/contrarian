"""
Give every existing key and every existing trace an owner.

Run once, by hand, after deploying ownership and before believing that `/runs`
shows the right thing:

    docker exec contrarian-contrarian-1 python backfill_owners.py --report
    docker exec contrarian-contrarian-1 python backfill_owners.py \
        --user 'Giovanni Spitale:giovanni.spitale@example.org:01ABC...:admin' \
        --user 'Nikola:nikola@example.org::' \
        --key 1=1 --key 2=1 --key 3=2 \
        --orphan-runs 1

Why a script and not a default at startup. Until now this service had one human,
so nothing recorded whose trace was whose; every existing row is genuinely
ambiguous, and a program that guesses would be inventing provenance for
verifications that may end up quoted somewhere. A script gets read before it is
run and prints what it did.

Three things it sets, in order:

  --user  NAME:EMAIL:BORANT_SUB:admin — create or update a person. The subject
          may be empty for someone who has no account on the gate yet: their
          key still needs an owner so their runs are theirs from the first call.
  --key   KEY_ID=USER_ID — whose key this is.
  --orphan-runs USER_ID — assign every run that still has no owner. Runs made
          after this point inherit the owner of the calling key automatically.

Nothing is overwritten silently: an existing owner is reported and left alone,
and --force is the only way past that.
"""
import argparse
import sys

from models import ApiKey, Run, SessionLocal, User


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", action="append", default=[],
                    metavar="NAME:EMAIL:SUB:admin")
    ap.add_argument("--key", action="append", default=[], metavar="KEY_ID=USER_ID")
    ap.add_argument("--orphan-runs", type=int, default=None, metavar="USER_ID")
    ap.add_argument("--force", action="store_true",
                    help="allow reassigning something that already has an owner")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()

    for spec in args.user:
        parts = (spec.split(":") + ["", "", "", ""])[:4]
        name, email, sub, flag = (p.strip() for p in parts)
        email = email.lower() or None
        sub = sub or None
        existing = None
        if sub:
            existing = db.query(User).filter(User.borant_sub == sub).first()
        if existing is None and email:
            existing = db.query(User).filter(User.email == email).first()
        if existing is None:
            u = User(name=name, email=email, borant_sub=sub,
                     is_admin=(flag.lower() == "admin"))
            db.add(u); db.commit(); db.refresh(u)
            print(f"  UTENTE   creato id={u.id} {name!r} sub={sub or '(nessuno)'} "
                  f"admin={u.is_admin}")
        else:
            changed = []
            if sub and not existing.borant_sub:
                existing.borant_sub = sub; changed.append("sub")
            if email and not existing.email:
                existing.email = email; changed.append("email")
            if flag.lower() == "admin" and not existing.is_admin:
                existing.is_admin = True; changed.append("admin")
            db.commit()
            print(f"  UTENTE   esiste id={existing.id} {existing.name!r}"
                  + (f", aggiornato: {', '.join(changed)}" if changed else ", invariato"))

    for spec in args.key:
        kid, _, uid = spec.partition("=")
        try:
            kid, uid = int(kid), int(uid)
        except ValueError:
            print(f"  SALTO    {spec!r}: serve la forma KEY_ID=USER_ID"); continue
        k = db.get(ApiKey, kid)
        if k is None:
            print(f"  ASSENTE  chiave {kid}"); continue
        if k.user_id and k.user_id != uid and not args.force:
            print(f"  CONFLITTO chiave {kid} ({k.name!r}) e' gia' di {k.user_id}, "
                  f"non la sposto. Usa --force se e' voluto."); continue
        k.user_id = uid
        db.commit()
        print(f"  CHIAVE   {kid} {k.name!r} -> utente {uid}")

    if args.orphan_runs is not None:
        rows = db.query(Run).filter(Run.user_id.is_(None)).all()
        for r in rows:
            r.user_id = args.orphan_runs
        db.commit()
        print(f"  RUN      {len(rows)} tracce senza proprietario -> utente {args.orphan_runs}")

    print("\n-- stato --")
    for u in db.query(User).order_by(User.id).all():
        nk = db.query(ApiKey).filter(ApiKey.user_id == u.id).count()
        nr = db.query(Run).filter(Run.user_id == u.id).count()
        print(f"  utente {u.id}: {u.name or u.email or '(senza nome)':<26} "
              f"admin={str(u.is_admin):<5} sub={u.borant_sub or '(nessuno)':<28} "
              f"chiavi={nk} tracce={nr}")
    orfane = db.query(Run).filter(Run.user_id.is_(None)).count()
    senza = db.query(ApiKey).filter(ApiKey.user_id.is_(None)).count()
    print(f"\n  tracce senza proprietario: {orfane}")
    print(f"  chiavi senza proprietario: {senza}")
    if orfane:
        print("  Una traccia senza proprietario non la vede nessuno — chiuso, ma inutile.")
    if senza:
        print("  Una chiave senza proprietario produce tracce che non vedra' nessuno.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
