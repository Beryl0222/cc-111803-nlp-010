import argparse
import json
from pathlib import Path

from .desk import assess
from .statements import classify_statement


def load_case(case_path, registry_path):
    case = json.loads(case_path.read_text(encoding='utf-8'))
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    return assess(case, registry)


def read_statement(text, registry, speaker=None):
    """按登记人员核对一句原话的性质，只返回线索，不生成排期。"""
    return classify_statement({'speaker': speaker, 'text': text}, registry.get('people'))


def main():
    parser = argparse.ArgumentParser(description='核对计划承诺和工位占用')
    parser.add_argument('--case', type=Path, default=Path('fixtures/work.json'))
    parser.add_argument('--registry', type=Path, default=Path('fixtures/business.json'))
    parser.add_argument('--text', help='只核对一句会议原话的发言性质')
    parser.add_argument('--speaker', help='说这句话的人；需与登记人员一致')
    args = parser.parse_args()
    try:
        registry = json.loads(args.registry.read_text(encoding='utf-8'))
        if args.text:
            result = read_statement(args.text, registry, args.speaker)
        else:
            result = assess(json.loads(args.case.read_text(encoding='utf-8')), registry)
    except (OSError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
