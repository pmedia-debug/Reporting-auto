import argparse
import logging
from metrics import run

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--today',help='YYYY-MM-DD; for period testing')
    args=p.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    run('monthly','tata_aig/config.yaml',args.dry_run,args.today)
