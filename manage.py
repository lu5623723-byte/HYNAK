import argparse, os
from launcher import load_env
load_env()
parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='cmd',required=True)
sub.add_parser('migrate');sub.add_parser('backup');p=sub.add_parser('restore');p.add_argument('archive');p.add_argument('destination')
p=sub.add_parser('reset-password');p.add_argument('username')
p=sub.add_parser('test');args=parser.parse_args()
if args.cmd=='test':
    import subprocess,sys
    raise SystemExit(subprocess.call([sys.executable,'-m','pytest','tests','-q']))
from app.db import migrate, Session, write_lock
from app.backup import backup,restore
if args.cmd=='migrate':migrate();print('Schema version 3 ready')
if args.cmd=='backup':print(backup(True))
if args.cmd=='restore':print(restore(args.archive,args.destination))
if args.cmd=='reset-password':
    import getpass
    from sqlalchemy import select
    from app.core import hash_password,audit
    from app.models import User,LoginSession
    with Session() as db:
        write_lock(db);u=db.scalar(select(User).where(User.username==args.username))
        if not u:raise SystemExit('User not found')
        password=getpass.getpass('New password (min 10): ')
        if password!=getpass.getpass('Repeat: '):raise SystemExit('Passwords differ')
        u.password=hash_password(password)
        for s in db.scalars(select(LoginSession).where(LoginSession.user_id==u.id)).all():db.delete(s)
        audit(db,u,'PASSWORD_RESET',u.id,'Local operator reset');db.commit();print('Password reset; existing sessions revoked.')
