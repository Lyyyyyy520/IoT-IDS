"""
检查 Hugging Face 上的 CICIoT2023 数据集是否含 IP 列。

用法:
    pip install datasets pyarrow   # 首次需装
    python training/check_ciciot.py
"""
import sys

def main():
    try:
        from datasets import load_dataset, get_dataset_config_names
    except ImportError:
        print('请先安装: pip install datasets pyarrow')
        return

    repo = 'lacg030175/CIC-IoT-2023'

    # 列出所有 config（random / random_3way）
    try:
        configs = get_dataset_config_names(repo)
        print(f'数据集 {repo} 的 configs: {configs}')
    except Exception as e:
        print(f'列 configs 失败: {e}')
        configs = ['random', 'random_3way']

    for cfg in configs:
        print(f'\n{"="*60}')
        print(f'加载 config: {cfg}')
        try:
            ds = load_dataset(repo, cfg, split='train', streaming=True)
            # 取第一条看列名
            sample = next(iter(ds))
            cols = list(sample.keys())
            print(f'列名({len(cols)}个): {cols}')

            # 检查 IP 相关列
            ip_cols = [c for c in cols if any(k in c.lower() for k in ['ip', 'addr', 'src', 'dst', 'source', 'destination'])]
            print(f'\n>>> IP相关列: {ip_cols}')
            label_cols = [c for c in cols if any(k in c.lower() for k in ['label', 'class', 'attack', 'type'])]
            print(f'>>> 标签相关列: {label_cols}')

            # 打印前几个字段的值
            print('\n>>> 样本前10个字段的值:')
            for k in cols[:10]:
                print(f'    {k}: {sample[k]}')
        except Exception as e:
            print(f'加载失败: {e}')


if __name__ == '__main__':
    main()
