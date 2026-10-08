# Pipeline: จากโมเดล 3D → sprite 8 ทิศ → เทียบการเดิน/โจมตี

```
Meshy (โมเดล) ─► Rig (Mixamo / Blender) ─► Animations ─► blender/render_8dir.py ─► renders/<project>/<name>/
                                                                                    │
                                     Playtest (เทียบกับตัวละครอื่น)    ◄────┘
```

## 1. ใส่กระดูก (Rig)

### ตัวละครทรงคน → Mixamo (ฟรี)

1. Export โมเดลจาก Meshy เป็น **FBX** หรือ **OBJ** (ท่า T-pose หรือ A-pose)
2. อัปโหลดเข้า <https://www.mixamo.com> → Auto-Rigger → วางจุด คาง / ข้อมือ / ข้อศอก / เข่า / เป้า
3. **ไฟล์แรก (มีโมเดล):** เลือกท่า Idle → Download
   - Format: **FBX Binary** · Skin: **With Skin** · Frames per second: **30** · Keyframe Reduction: **none**
4. **ท่าอื่น ๆ:** Download แบบ **Without Skin** (ไฟล์เล็กกว่า)
   - Walk / Run: **ไม่ต้องติ๊ก "In Place"** สคริปต์จะวัดระยะที่ตัวละครเดินไปได้จริง แล้วคำนวณเป็น **stride speed** (m/s) ให้เอง จากนั้นจะดึงตัวละครกลับมาไว้กลางเฟรม
5. ตั้งชื่อไฟล์เป็น `ชื่อ@ท่า.fbx` เช่น `orc.fbx`, `orc@walk.fbx`, `orc@attack.fbx` สคริปต์จะใช้ส่วนหลัง `@` เป็นชื่อท่า

### มังกร Vereth → Blender (Rigify) หรือไลบรารีท่าสัตว์ของ Meshy

- **Meshy:** ถ้าได้โมเดลพร้อมท่ามาแล้ว ให้ export เป็น GLB/FBX ที่มี animation แล้วใช้ `--files` ได้เลย
- **Blender:** เปิด add-on Rigify → Add ▸ Armature ▸ *Basic Quadruped* (หรือ *Wolf*) เป็นโครงเริ่มต้น
  - เพิ่มกระดูกปีก (โซ่กระดูกแบบ `limbs.super_limb` หรือ `basic.copy_chain`) และหาง (`spines.basic_tail`)
  - Generate Rig → ผูก mesh (Ctrl+P ▸ With Automatic Weights)
  - ทำท่าเป็น **Action** แยกกัน เช่น `Idle`, `Walk`, `Bite`, `Breath`
  - ท่า Walk: ถ้าให้ root เคลื่อนที่ไปข้างหน้าจริง สคริปต์จะวัด stride speed ให้ ถ้าทำแบบอยู่กับที่ ให้กรอกค่าเองใน Playtest
  - บันทึกเป็น `vereth.blend`

## 2. Render 8 ทิศ

ต้องใช้ Blender 4.2 ขึ้นไป (ทดสอบแล้วกับ 5.2) ตัวอย่างใน PowerShell:

```powershell
$B = "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"   # หรือเวอร์ชันที่ติดตั้งไว้

# Mixamo
& $B -b -P blender/render_8dir.py -- --name orc --files orc.fbx orc@walk.fbx orc@attack.fbx --ppm 64 --size 256 --out renders/my_characters

# .blend ที่ rig เอง (เลือกท่าตามชื่อ action)
& $B -b vereth.blend -P blender/render_8dir.py -- --name vereth --actions Idle Walk Bite --ppm 64 --size 512 --out renders/my_characters
```

| ตัวเลือก | ค่าแนะนำ | ความหมาย |
|---|---|---|
| `--ppm` | **ใช้ค่าเดียวกันทุกตัว** เช่น 64 | pixel ต่อเมตร ถ้าไม่ใส่ แต่ละตัวจะถูกย่อหรือขยายให้เต็มเฟรม ทำให้เทียบขนาดกันไม่ได้ |
| `--size` | 256 (คน), 512 (มังกร) | ขนาดเฟรม เปลี่ยนเฉพาะขนาดเฟรมได้ แต่ ppm ต้องเท่าเดิม |
| `--elevation` | 30 | มุมกล้องก้มลง 30° ทำให้พื้นเป็น isometric แบบ 2:1 พอดีกับกริด |
| `--step` | 2 | render ทุก 2 เฟรม (30fps → 15fps ≈ 67ms ต่อเฟรม ใกล้กับจังหวะ sprite แบบคลาสสิก) |
| `--mirror` | ใช้กับตัวที่สมมาตร | render แค่ 5 ทิศ แล้ว flip ให้ได้ NE/E/SE ลดงาน 37% (แต่อาวุธจะสลับไปอยู่มืออีกข้าง) |
| `--facing=-Y` | -Y | ทิศที่โมเดลหันหน้าตอนท่าพัก (Mixamo = -Y) ถ้าภาพที่ได้หันผิดทิศ ให้ลอง `--facing=+Y` / `+X` / `-X` (ต้องมี `=` เพราะ -Y ขึ้นต้นด้วยขีด) |
| `--directions S E --max-frames 4` | – | ใช้ทดสอบเร็ว ๆ ก่อน render จริง |

ใส่ `--out renders/<project>` (เช่น `renders/my_characters`) เพื่อให้ Playtest เห็นตัวละคร · ผลลัพธ์จะอยู่ที่ `renders/<project>/<name>/`:
- `frames/<ท่า>/<ทิศ>/000.png` คือเฟรมทีละไฟล์
- `render_manifest.json` เก็บขนาดเฟรม, pivot, ppm, ความเร็ว root motion และเฟรมที่เท้าแตะพื้น (`step_l` / `step_r`)
- เมื่อเปิด Playtest เครื่องมือจะ pack ทุกอย่างเป็น `sheets/<ท่า>.png` (แถว = ทิศ S, SW, W, NW, N, NE, E, SE · คอลัมน์ = เฟรม) และ `character.json` ให้อัตโนมัติ

## 3. เทียบใน Playtest

เปิดแท็บ **Playtest** แล้วเพิ่มตัวละครจาก *Your renders* หรือ *Sprite library*

- **Playground:** ใช้ WASD หรือปุ่มลูกศรเดิน 8 ทิศ, คลิกพื้นเพื่อเดินไปตรงนั้น (เดินทแยงก่อนแล้วค่อยเดินตรง), กด Space เพื่อโจมตีหุ่นซ้อมที่อยู่ใกล้ที่สุด หุ่นจะกระพริบตรงเฟรมที่ดาเมจเกิด
- **Lineup:** ทุกตัวเล่นท่าเดียวกันในทิศเดียวกันพร้อมกัน
  - *walk*: พื้นใต้เท้าแต่ละตัวเลื่อนด้วยความเร็วในเกม ถ้าเท้าไถลแปลว่าความเร็วในเกมไม่ตรงกับก้าวของ animation
  - *attack*: เทียบจังหวะ ● HIT ของแต่ละตัว
- **ตาราง:** แสดงรอบการเดิน (ms), stride speed เทียบกับความเร็วในเกม, ระยะต่อรอบ, % การไถล, เวลาโจมตี, เฟรมที่ดาเมจเกิด และส่วนสูง
- **Save** บันทึกค่าลง `renders/<project>/<name>/lab_config.json` · **Export JSON** ส่งออกข้อมูลสำหรับ engine (ความเร็ว, hit frame, ระยะโจมตี, timing ของทุกท่า)

## 4. หลักที่ใช้ (sprite เกม 2D แบบคลาสสิก)

| เรื่อง | แบบคลาสสิก | นำมาใช้กับเรา |
|---|---|---|
| โครงสร้าง | action = ประเภทท่า × 8 + ทิศ (0=S แล้ววนตามเข็มนาฬิกา) มอนสเตอร์ส่วนใหญ่มี 5 ท่า: idle, walk, attack, damage, die | ใช้ลำดับทิศเดียวกัน และควรมีครบ 5 ท่านี้ |
| 8 ทิศ | ท่าส่วนใหญ่วาดจริงแค่ **2 รูป** (หน้าเฉียงกับหลังเฉียง) แล้ว flip | `--mirror` คือเวอร์ชัน 3D ของเทคนิคเดียวกัน |
| เวลาต่อเฟรม | หน่วย 25ms · ทั่วไป: idle 100ms, walk 75ms, attack 62.5ms | `--step 2` ให้ 67ms ต่อเฟรม |
| จำนวนเฟรม (ทั่วไป) | idle 8 · walk 8 (600ms/รอบ) · attack 12 (800ms) · damage 5 · die 18 | เป็นค่าตั้งต้นที่ดีสำหรับเกมของเรา |
| จังหวะดาเมจ | event `atk` อยู่ราว **59%** ของท่า | Playtest ใช้ 59% เป็นค่าเริ่มต้นเมื่อยังไม่ได้กำหนด hit frame |
| เสียงฝีเท้า | ท่าเดินมี event เสียงในเฟรมที่เท้าแตะพื้น | สคริปต์ใส่ `step_l` / `step_r` ให้อัตโนมัติ |
| Pivot | ทุก layer อ้างอิงจากจุด (0,0) ที่เท้า | ทุกเฟรมที่ render ให้เท้าอยู่ที่ pixel เดียวกัน |
| Anchor | จุดเกาะสำหรับหัว หมวก และอาวุธ | แนวคิดเดียวกับ slot ติดไอเท็ม |

