"""本地账单核对：默认只预览；--apply 才确定性写入。CSV 不含正文/音频/密钥。"""
import argparse,csv,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.models.database import SessionLocal
from app.services.billing import reconcile

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('csv_file',help='usage_event_id,amount_cny,reference 三列')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    rows=list(csv.DictReader(Path(args.csv_file).open()))
    with SessionLocal() as db:
        for row in rows:
            if args.apply:
                reconcile(db,int(row['usage_event_id']),row['amount_cny'],row['reference'])
    print(f'{len(rows)} rows '+('reconciled' if args.apply else 'previewed; no changes'))
