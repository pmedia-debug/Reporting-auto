from pathlib import Path
import yaml

def main():
    counts = {'xovi_com': 13, 'xovi_de': 12, 'solusvm': 3, 'cometbackup': 21}
    for name, number in counts.items():
        cfg = yaml.safe_load((Path(__file__).parent / 'configs' / f'{name}.yaml').read_text())
        pages = cfg['pages']
        assert len(pages) == number, (name, len(pages))
        assert [p['heading_row'] for p in pages] == [17+7*i for i in range(number)]
        assert cfg['report']['first_week_column'] == 'L'
        assert cfg['report']['date_header_row'] == 2
        assert cfg['report']['week_start'] == 'month_day_1_7'
        print(f'{name}: {11+5*number} metrics; final row {17+7*(number-1)+5}')
if __name__ == '__main__':
    main()
