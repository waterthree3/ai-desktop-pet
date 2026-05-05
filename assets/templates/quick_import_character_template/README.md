# Quick Import Character Template

这个目录是给普通用户用的角色导入模板。

## 怎么用

1. 复制整个 `quick_import_character_template` 文件夹。
2. 把复制后的文件夹改成你自己的角色名，例如 `my_slime`。
3. 进入这个文件夹。
4. 删除 `.placeholder` 文件。
5. 按相同文件名放入你自己的真实素材。
6. 运行一键导入命令：

```powershell
python scripts/quick_import_character.py `
  --source-dir C:\path\to\my_slime `
  --asset-id my_slime `
  --label 我的史莱姆 `
  --set-current
```

## 三套模板

- `minimal/`
  最低可运行版本，只需要参考图和 `idle`。

- `recommended/`
  推荐新手先准备的版本，能比较快体验桌宠。

- `full/`
  较完整的角色包素材命名模板。

## 注意

- 程序真正读取的是你替换进去的真实图片/视频文件。
- `.placeholder` 文件只是占位说明，程序不会把它们当素材导入。
