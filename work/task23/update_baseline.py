import os
root = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
files = [os.path.join(root, "tests", "data", "photo_plan_ab_baseline.json"),
         os.path.join(root, "tests", "data", "photo_plan_ab_baseline_room_off.json")]
old = "她正躺在/坐在床上或沙发上，盖着被子"
new = "她正躺在床上，盖着被子"
for path in files:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    count = text.count(old)
    if count:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(old, new))
    print(path, "replaced", count)
