import re

input_file = "training_data_pool.txt"
output_file = "training_data_pool_clean.txt"

pattern = r'^用户 .+ 小焦 .+'

with open(input_file, "r", encoding="utf-8") as f:
    lines = f.readlines()

cleaned = []
for line in lines:
    line = line.strip()
    if re.match(pattern, line):
        cleaned.append(line)

print(f"原始行数: {len(lines)}")
print(f"有效行数: {len(cleaned)}")

with open(output_file, "w", encoding="utf-8") as f:
    f.write("\n".join(cleaned))