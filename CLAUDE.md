# ghoul-data — パチンコ実機データ収集（L 東京喰種）

PAPIMO（スタジアム二俣川店 `HALL=00042031`）の「L 東京喰種」6台を毎週スクレイプし、
GitHub Pages で公開する自走パイプライン。**launchd 定時実行のため Documents 外（ホーム直下）に置く**（TCC保護回避）。移動しないこと。

- 公開URL: https://freeeeeellc.github.io/ghoul-tokyo-data/（リポジトリ: freeeeeellc/ghoul-tokyo-data）
- 定時実行: launchd `com.ghoul-data.update`（毎週月曜 10:00）。ログ: `update.log`

## パイプライン（update.py が一気通貫）
スクレイプ → 差枚をスランプグラフ画像から**画素復元**（精度±約150枚） → `archive.json` に蓄積 → `index.html` / `data.csv` 再生成 → git push。

- ローカル確認は `python3 update.py --no-push`（pushしない）
- `--out <名前>` で期間別ページ（既定 index → index.html / data.csv）

## 触るときの注意
- `archive.json` は蓄積データの正本。壊すと過去分が消える（編集前にコピーを取る）
- スクレイプ先のDOM/画像仕様が変わると画素復元が狂う。数値が異常（±150枚を大きく超える乖離）なら update.log と元画像を確認
- 週次実行の確認: `launchctl list | grep ghoul-data` / 即時テスト: `launchctl kickstart gui/$(id -u)/com.ghoul-data.update`
