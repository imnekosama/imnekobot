# 导入必要的标准库和 telegram bot API 模块
import json  # 用于读写配置文件（config.json, blacklist.json）
import logging  # 用于记录日志，方便调试
import os  # 用于文件路径和文件是否存在判断
import time  # 用于时间戳获取和比较
from datetime import datetime  # 用于处理禁言时间显示
from pathlib import Path  # 目前未用上，可用于文件路径处理
from collections import defaultdict  # 用于记录用户投稿时间统计
from functools import partial  # 用于向 job_queue 调度传参
from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault  # 在主函数中设置管理员专属命令菜单，清除默认全员菜单
import html  # 用于 HTML 转义
# 导入 Telegram 相关功能模块
from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# 机器人版本号
BOT_VER ="v1.1.2"

# 定义配置文件路径
CONFIG_PATH = "config.json"
BLACKLIST_PATH = "blacklist.json"
WELCOME_IMG_PATH = "welcome.jpg"  # 欢迎图默认路径
REPLY_IMG_PATH = "reply_banner.jpg"  # 自动回复图像储存路径

# 定义缓存变量
MEDIA_GROUP_CACHE = {}  # 用于收集媒体组的所有消息
POST_COUNTER = defaultdict(list)  # 用于记录用户投稿的时间戳，用于频率限制

# 通用 JSON 文件读取函数
# 如果读取失败（比如文件不存在），就返回默认值
def load_json(path, default={}):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

# 通用 JSON 文件写入函数
def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# 读取配置和黑名单
config = load_json(CONFIG_PATH)
blacklist = load_json(BLACKLIST_PATH)

# 从 config 中获取必要信息
TOKEN = config.get("token")
ADMIN_ID = config.get("admin_id")
AUTO_REPLY = config.get("auto_reply", "感谢你的投稿，我们已收到！")
WELCOME_MSG = config.get("welcome_message", "欢迎使用投稿机器人！")
WELCOME_BTNS = config.get("welcome_buttons", [])
POST_LIMIT_CFG = config.get("post_limit", {"enabled": False, "count": 30})
BUTTON_LAYOUT = config.get("button_layout", {"row": 2, "col": 2})

# ✅ 从 safe_send.py 模块导入 safe_send 函数
from safe_send import safe_send
# ✅ 从 safe_send.py 模块导入 safe_send_image 函数
from safe_send import safe_send_image
# ✅ 向 safe_send.py 传递 ADMIN_ID
from safe_send import set_admin_id
set_admin_id(config.get("admin_id"))


# 格式化剩余时间为“xx秒/分钟/小时/天”的形式
def format_time_left(until_timestamp):
    # 如果是永久禁言，直接返回文字
    if until_timestamp == float('inf'):
        return "永久"
    seconds = int(until_timestamp - time.time())
    if seconds <= 0:
        return "已过期"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days: parts.append(f"{days}天")
    if hours: parts.append(f"{hours}小时")
    if minutes: parts.append(f"{minutes}分钟")
    if seconds: parts.append(f"{seconds}秒")
    return " ".join(parts)


# 构建带按钮的 InlineKeyboard
def build_inline_keyboard(buttons, row_size=2):
    keyboard = []
    for i in range(0, len(buttons), row_size):
        row = [InlineKeyboardButton(text=btn['text'], url=btn['url']) for btn in buttons[i:i+row_size]]
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


# 检查用户是否被禁言，如果禁言已过期则自动清除记录
def is_user_banned(user_id):
    info = blacklist.get(str(user_id))  # 确保 user_id 是字符串
    if not info:
        return False, 0  # 没有记录，未被禁言
    until = info.get("until", 0)
    # 如果禁言已过期（且不是永久禁言），自动解除并保存
    if until != float('inf') and until < time.time():
        blacklist.pop(str(user_id))
        save_json(BLACKLIST_PATH, blacklist)
        return False, 0
    return True, until


# 检查用户是否超过投稿限制
def check_post_limit(user_id):
    if not POST_LIMIT_CFG.get("enabled"):
        return True, None
    now = time.time()
    POST_COUNTER[user_id] = [t for t in POST_COUNTER[user_id] if now - t < 3600]
    if len(POST_COUNTER[user_id]) >= POST_LIMIT_CFG["count"]:
        return False, POST_LIMIT_CFG["count"]
    POST_COUNTER[user_id].append(now)
    return True, None


# 判断欢迎图片是否存在
def has_welcome_image():
    return os.path.exists(WELCOME_IMG_PATH)

# 判断自动回复图片是否存在
def has_reply_image():
    return os.path.exists(REPLY_IMG_PATH)


# 用户投稿处理函数
async def handle_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    message = update.message
    # 如果是管理员发的投稿，直接忽略
    if str(user_id) == str(ADMIN_ID):
        return
    # 检查是否禁言
    banned, until = is_user_banned(user_id)
    if banned:
        time_left = format_time_left(until)
        reason = blacklist.get(user_id, {}).get("reason", "")
        await message.reply_text(f"你已被禁言，剩余时间：{time_left}" + (f"\n原因：{reason}" if reason else ""))
        return
    # 检查投稿频率限制
    allowed, limit = check_post_limit(user_id)
    if not allowed:
        await message.reply_text(f"你已超过每小时{limit}次投稿限制，请稍后再试。")
        return
    # 构造用户信息字符串（点击用户名可跳转资料，ID 可复制）
    caption_info = f"来自: [{user.full_name}](tg://user?id={user.id})  |  ID: `{user.id}`"
    # 如果是媒体组，进行缓存收集和延迟转发
    if message.media_group_id:
        group_id = message.media_group_id
        MEDIA_GROUP_CACHE.setdefault(group_id, []).append(message)
        # 如果是首次收到该媒体组，则安排一个延时任务 2 秒后执行处理
        if len(MEDIA_GROUP_CACHE[group_id]) == 1:
            context.job_queue.run_once(
                partial(process_media_group, group_id=group_id, user=user, caption_info=caption_info),
                when=2,
                name=str(group_id)
            )
        return
    try:
        # ✅ 改为保存返回值 result，用于判断发送成功
        result = None  # 用于保存每种投稿类型的转发结果
        # 普通文字消息
        if message.text:
            full_text = f"{caption_info}\n\n{message.text}"
            result = await safe_send(
                context.bot,
                context.bot.send_message,
                chat_id=ADMIN_ID,
                text=full_text,
                parse_mode=ParseMode.MARKDOWN,
                user_info=caption_info,
                user_id=user.id
            )
        # 图片投稿
        elif message.photo:
            full_caption = f"{caption_info}\n\n{message.caption or ''}"
            result = await safe_send(
                context.bot,
                context.bot.send_photo,
                chat_id=ADMIN_ID,
                photo=message.photo[-1].file_id,
                caption=full_caption,
                parse_mode=ParseMode.MARKDOWN,
                user_info=caption_info,
                user_id=user.id,
                retries=5,
                delay=3
            )
        # 视频投稿
        elif message.video:
            full_caption = f"{caption_info}\n\n{message.caption or ''}"
            result = await safe_send(
                context.bot,
                context.bot.send_video,
                chat_id=ADMIN_ID,
                video=message.video.file_id,
                caption=full_caption,
                parse_mode=ParseMode.MARKDOWN,
                user_info=caption_info,
                user_id=user.id,
                retries=10,
                delay=5
            )
        # 文档投稿
        elif message.document:
            full_caption = f"{caption_info}\n\n{message.caption or ''}"
            result = await safe_send(
                context.bot,
                context.bot.send_document,
                chat_id=ADMIN_ID,
                document=message.document.file_id,
                caption=full_caption,
                parse_mode=ParseMode.MARKDOWN,
                user_info=caption_info,
                user_id=user.id,
                retries=5,
                delay=3
            )
        # 其他类型：复制消息
        else:
            result = await safe_send(
                context.bot,
                context.bot.copy_message,
                chat_id=ADMIN_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                user_info=caption_info,
                user_id=user.id
            )
        # ✅ 仅当 result 为真时，发送“投稿成功”自动回复（图文 or 文本）
        if result:
            if has_reply_image():
                # ✅ 使用 safe_send_image() 安全发送图片，避免因网络重试文件句柄失效
                await safe_send_image(
                    bot=context.bot,
                    chat_id=user.id,
                    file_path=REPLY_IMG_PATH,
                    caption=config.get("auto_reply", "🎉投递成功，感谢投稿！管理员会尽快进行审核。"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"]),
                    user_info=caption_info,
                    user_id=user.id
                )
            else:
                # ✅ 无图片时仍使用 safe_send 发送纯文本
                await safe_send(
                    context.bot,
                    context.bot.send_message,
                    chat_id=user.id,
                    text=config.get("auto_reply", "🎉投递成功，感谢投稿！管理员会尽快进行审核。"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"]),
                    user_info=caption_info,
                    user_id=user.id
                )
        else:
            # ❌ 如果 result 为 None，说明转发失败，告知投稿用户
            await safe_send(
                context.bot,
                context.bot.send_message,
                chat_id=user.id,
                text="❌ 很抱歉，您的投稿发送失败了，请稍后再试。",
                parse_mode=ParseMode.HTML,
                reply_markup=build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"]),
                user_info=caption_info,
                user_id=user.id
            )
    except Exception as e:
        logging.error(f"转发失败（异常）: {e}")


# 延迟处理媒体组投稿（在所有组内消息收集完后统一转发）
async def process_media_group(context: ContextTypes.DEFAULT_TYPE, group_id, user, caption_info):
    messages = MEDIA_GROUP_CACHE.pop(group_id, [])  # 取出该组的所有消息
    media = []
    # 获取用户附加的 caption（通常只有一条消息包含）
    user_caption = ""
    for m in messages:
        if m.caption:
            user_caption = m.caption
            break
    # 拼接完整 caption 信息（第一条媒体用）
    full_caption = f"{caption_info}\n\n{user_caption}".strip()
    for i, m in enumerate(messages):
        if m.photo:
            media.append(InputMediaPhoto(
                media=m.photo[-1].file_id,
                caption=full_caption if i == 0 else None,
                parse_mode=ParseMode.MARKDOWN if i == 0 else None
            ))
        elif m.video:
            media.append(InputMediaVideo(
                media=m.video.file_id,
                caption=full_caption if i == 0 else None,
                parse_mode=ParseMode.MARKDOWN if i == 0 else None
            ))
        elif m.document:
            media.append(InputMediaDocument(
                media=m.document.file_id,
                caption=full_caption if i == 0 else None,
                parse_mode=ParseMode.MARKDOWN if i == 0 else None
            ))
    try:
        # 发送媒体组
        # ✅ 改为使用 safe_send，并接收 result 判断发送结果
        result = await safe_send(
            context.bot,
            context.bot.send_media_group,
            chat_id=ADMIN_ID,
            media=media,
            user_info=caption_info,
            user_id=user.id,
            retries=10,  # 👈 设置重试次数
            delay=5       # 👈 每次重试间隔
        )
        # ✅ 仅当 result 为真时，发送“投稿成功”自动回复（图文 or 文本）
        if result:
            if has_reply_image():
                # ✅ 使用 safe_send_image() 安全发送图片，避免因网络重试文件句柄失效
                await safe_send_image(
                    bot=context.bot,
                    chat_id=user.id,
                    file_path=REPLY_IMG_PATH,
                    caption=config.get("auto_reply", "🎉投递成功，感谢投稿！管理员会尽快进行审核。"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"]),
                    user_info=caption_info,
                    user_id=user.id
                )
            else:
                # ✅ 无图片时仍使用 safe_send 发送纯文本
                await safe_send(
                    context.bot,
                    context.bot.send_message,
                    chat_id=user.id,
                    text=config.get("auto_reply", "🎉投递成功，感谢投稿！管理员会尽快进行审核。"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"]),
                    user_info=caption_info,
                    user_id=user.id
                )
        else:
            # ❌ 失败：通知投稿用户失败信息
            await safe_send(
                context.bot,
                context.bot.send_message,
                chat_id=user.id,
                text="❌ 很抱歉，您的投稿发送失败了，请稍后再试。",
                parse_mode=ParseMode.HTML,
                reply_markup=build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"]),
                user_info=caption_info,
                user_id=user.id
            )
    except Exception as e:
        logging.error(f"媒体组发送失败: {e}")


# 管理员回复投稿者（通过回复投稿消息）
async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    # 配合 safe_send 给 caption_info 赋值管理员信息
    caption_info = f"📮本条消息来自管理员: [{message.from_user.full_name}](tg://user?id={message.from_user.id})  |  ⛔️请勿回复本消息！"
    # 只允许管理员操作，且必须是“回复消息”形式
    if not message.reply_to_message or not message.text:
        return
    # 提取被回复消息中包含的用户 ID
    lines = []
    if message.reply_to_message.caption:
        lines = message.reply_to_message.caption.splitlines()
    elif message.reply_to_message.text:
        lines = message.reply_to_message.text.splitlines()
    target_id = None
    for line in lines:
        if "ID:" in line:
            try:
                target_id = line.split("ID:")[-1].strip().strip("`").split()[0]
                break
            except:
                continue
    if not target_id:
        await message.reply_text("⚠️ 未找到目标用户 ID，可能不是投稿消息")
        return
    try:
        # ✅ 改为使用 safe_send 并加入 result 判断：
        result = await safe_send(
            context.bot,
            context.bot.send_message,
            chat_id=int(target_id),
            text=f"{caption_info}\n\n{message.text}",  # ✅ 显式把“来自管理员...”加到正文
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"]),
            user_info=caption_info,
            user_id=message.from_user.id  # ✅ 修正为当前发信管理员的 ID
        )
        # ✅ 根据结果向管理员发送确认消息
        if result:
            await message.reply_text("✅ 已发送给投稿用户")
        else:
            await message.reply_text("❌ 发送失败，可能是网络问题或用户屏蔽了机器人")
    except Exception as e:
        logging.error(f"管理员回复失败: {e}")
        await message.reply_text("❌ 发送失败，发生异常错误")


# 管理员禁言用户：/ban 用户ID 时长(分钟) [原因]
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    args = context.args
    # 参数数量不足，至少需要 用户ID 和 禁言分钟数
    if len(args) < 2:
        await update.message.reply_text("用法：/ban [用户ID] [时长(分钟)] [原因(可选)]\n\n时长填 0 表示永久禁言")
        return
    user_id = args[0]
    # 检查第二个参数是否是整数（防止 `/ban 123 abc`）
    try:
        minutes = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ 无效的禁言时长，必须是数字（单位为分钟）")
        return
    reason = " ".join(args[2:]) if len(args) > 2 else ""
    until = time.time() + minutes * 60 if minutes > 0 else float('inf')
    # 主动获取用户资料（避免昵称未知）
    try:
        user_obj = await context.bot.get_chat(user_id)
        name = user_obj.full_name
        username = user_obj.username or "无"
    except:
        name = "未知"
        username = "无"
    user_info = {
        "user_id": user_id,
        "until": until,
        "time_str": datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M") if until != float('inf') else "永久",
        "name": name,
        "username": username,
        "reason": reason
    }
    blacklist[user_id] = user_info
    save_json(BLACKLIST_PATH, blacklist)
    await update.message.reply_text(
        f"✅ 已禁言用户 {user_id}（{name}），时长：{user_info['time_str']}"
        + (f"\n📌 原因：{reason}" if reason else "")
    )


# 解除禁言用户：/unban 用户ID
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    args = context.args
    if not args:
        await update.message.reply_text("用法：/unban 用户ID")
        return
    user_id = args[0]
    if user_id in blacklist:
        blacklist.pop(user_id)
        save_json(BLACKLIST_PATH, blacklist)
        await update.message.reply_text(f"已解除禁言用户 {user_id}")
    else:
        await update.message.reply_text("该用户不在禁言列表中")


# 查看所有被禁言用户：/banned
async def list_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    if not blacklist:
        await update.message.reply_text("当前无被禁言用户")
        return
    text = "<b>🔒 当前被禁言用户列表：</b>\n\n"
    for uid, info in blacklist.items():
        name = html.escape(info.get("name", "未知"))
        username = html.escape(info.get("username", "无"))
        reason = html.escape(info.get("reason", ""))
        left = format_time_left(info["until"]) if info["until"] != float("inf") else "永久"

        text += (
            f"👤 {name} (@{username})\n"
            f"ID: <code>{uid}</code>\n"
            f"剩余时间：{left}" + (f"\n原因：{reason}" if reason else "") + "\n\n"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# 开启或关闭投稿频率限制：/limit [on/off 次数]
async def toggle_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    args = context.args
    if not args:
        # 不带参数指令默认显示当前状态
        await update.message.reply_text(f"✅ 投稿限制已启用，每小时限制 {POST_LIMIT_CFG['count']} 次" if POST_LIMIT_CFG['enabled'] else "✅ 投稿限制已关闭")
        return
    else:
        POST_LIMIT_CFG["enabled"] = args[0].lower() == "on"
        POST_LIMIT_CFG["count"] = int(args[1]) if len(args) > 1 else 30
    config["post_limit"] = POST_LIMIT_CFG
    save_json(CONFIG_PATH, config)
    await update.message.reply_text(f"✅ 投稿限制已启用，每小时限制 {POST_LIMIT_CFG['count']} 次" if POST_LIMIT_CFG['enabled'] else "✅ 投稿限制已关闭")


# 设置欢迎文本内容：/setwelcome 欢迎文字
async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    text = update.message.text_html.replace("/setwelcome", "").strip()
    if not text:
        await update.message.reply_text("请提供欢迎内容。用法：/setwelcome 欢迎文本")
        return
    config["welcome_message"] = text
    save_json(CONFIG_PATH, config)
    await update.message.reply_text("✅ 欢迎信息已更新。")


# === 通用等待操作状态 ===
pending_action = {
    "type": None,      # 等待任务类型：如 "welcome_image"
    "user_id": None    # 触发该任务的管理员 ID
}


# 通用取消指令：/cancel 可取消任何等待状态
async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    if pending_action["type"]:
        desc = pending_action["type"]
        pending_action["type"] = None
        pending_action["user_id"] = None
        await update.message.reply_text(f"✅ 操作已取消（类型：{desc}）")
    else:
        await update.message.reply_text("📭 当前无待取消的操作")


#管理员图片监听器接收
async def handle_admin_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        return
    # 判断当前处于哪种等待状态，并委托给对应函数
    if pending_action["type"] == "welcome_image":
        await set_welcome_image(update, context)
    elif pending_action["type"] == "reply_image":
        await set_reply_image(update, context)
    else:
        # 如果没有等待任务，不处理图片
        await update.message.reply_text("🤖 当前未在等待任何图片设置指令，图片已忽略")


# 管理员输入 /setwelcomeimg，进入等待欢迎图片模式
async def start_set_welcome_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    pending_action["type"] = "welcome_image"
    pending_action["user_id"] = update.effective_user.id
    await update.message.reply_text("✅ 请发送欢迎图片，我将自动设置为欢迎图。如需取消，请发送 /cancel")


# 管理员发送图片后，若处于等待设置欢迎图状态则保存
async def set_welcome_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (pending_action["type"] == "welcome_image" and update.effective_user.id == pending_action["user_id"]):
        return  # 非等待状态或非触发管理员，不处理
    if not update.message.photo:
        await update.message.reply_text("请发送图片")
        return
    # 获取并保存图片
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    await file.download_to_drive(WELCOME_IMG_PATH)
    # 重置状态
    pending_action["type"] = None
    pending_action["user_id"] = None
    await update.message.reply_text("✅ 欢迎图片已成功设置！")


# 清除当前设置的欢迎图片
async def clear_welcome_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    if os.path.exists(WELCOME_IMG_PATH):
        os.remove(WELCOME_IMG_PATH)
        await update.message.reply_text("✅ 已移除欢迎图片")
    else:
        await update.message.reply_text("⚠️ 当前无欢迎图片")


# 管理员输入 /setreplyimg，进入等待设置自动回复图片状态
async def start_set_reply_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    pending_action["type"] = "reply_image"
    pending_action["user_id"] = update.effective_user.id
    await update.message.reply_text("✅ 请发送自动回复图片，如需取消请输入 /cancel")


# 管理员发送图片后，若处于等待设置自动回复图状态则保存
async def set_reply_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (pending_action["type"] == "reply_image" and update.effective_user.id == pending_action["user_id"]):
        return
    if not update.message.photo:
        await update.message.reply_text("请发送图片")
        return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    await file.download_to_drive(REPLY_IMG_PATH)
    pending_action["type"] = None
    pending_action["user_id"] = None
    await update.message.reply_text("✅ 自动回复图片已设置！")


# 清除当前设置的自动回复图片
async def clear_reply_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    if os.path.exists(REPLY_IMG_PATH):
        os.remove(REPLY_IMG_PATH)
        await update.message.reply_text("✅ 已移除自动回复图片")
    else:
        await update.message.reply_text("⚠️ 当前无自动回复图片")


# 设置自动回复文本内容：/setautoreply 欢迎文字
async def set_autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    text = update.message.text_html.replace("/setautoreply", "").strip()
    if not text:
        await update.message.reply_text("请提供自动回复内容。用法：/setautoreply 自动回复文本")
        return
    config["auto_reply"] = text
    save_json(CONFIG_PATH, config)
    await update.message.reply_text("✅ 自动回复信息已更新。")


# 设置按钮布局：/sortbuttons 2x2
async def sort_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    args = context.args[0].lower().split("x") if context.args else []
    if len(args) != 2 or not all(x.isdigit() for x in args):
        await update.message.reply_text("用法：/sortbuttons 2x2")
        return
    BUTTON_LAYOUT["row"], BUTTON_LAYOUT["col"] = int(args[0]), int(args[1])
    config["button_layout"] = BUTTON_LAYOUT
    save_json(CONFIG_PATH, config)
    await update.message.reply_text(f"✅ 按钮布局更新为：{BUTTON_LAYOUT['row']}行×{BUTTON_LAYOUT['col']}列")


# 显示当前设置的欢迎按钮列表
async def list_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    msg = "📌 当前欢迎按钮：\n"
    for i, btn in enumerate(WELCOME_BTNS, 1):
        msg += f"{i}. {btn['text']} → {btn['url']}\n"
    await update.message.reply_text(msg)


# 添加新按钮：/addbutton 文本 URL
async def add_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    if len(context.args) < 2:
        await update.message.reply_text("用法：/addbutton 文本 URL")
        return
    text, url = context.args[0], context.args[1]
    WELCOME_BTNS.append({"text": text, "url": url})
    config["welcome_buttons"] = WELCOME_BTNS
    save_json(CONFIG_PATH, config)
    await update.message.reply_text(f"✅ 按钮已添加：{text} → {url}")


# 删除按钮：/delbutton 序号
async def del_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("用法：/delbutton 序号")
        return
    idx = int(context.args[0]) - 1
    if 0 <= idx < len(WELCOME_BTNS):
        removed = WELCOME_BTNS.pop(idx)
        config["welcome_buttons"] = WELCOME_BTNS
        save_json(CONFIG_PATH, config)
        await update.message.reply_text(f"✅ 已删除按钮：{removed['text']}")
    else:
        await update.message.reply_text("❌ 无效序号")


# 编辑已有按钮：/editbutton 序号 文本 URL
async def edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    if len(context.args) < 3 or not context.args[0].isdigit():
        await update.message.reply_text("用法：/editbutton 序号 文本 URL")
        return
    idx = int(context.args[0]) - 1
    text, url = context.args[1], context.args[2]
    if 0 <= idx < len(WELCOME_BTNS):
        WELCOME_BTNS[idx] = {"text": text, "url": url}
        config["welcome_buttons"] = WELCOME_BTNS
        save_json(CONFIG_PATH, config)
        await update.message.reply_text(f"✅ 按钮已修改为：{text} → {url}")
    else:
        await update.message.reply_text("❌ 无效序号")


# 用户使用 /start 指令时看到的欢迎信息
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # 构造投稿用户信息（用于失败通知）
    caption_info = f'<a href="tg://user?id={user.id}">{user.full_name}</a> | ID: <code>{user.id}</code>'
    # 获取欢迎消息内容和按钮布局
    welcome_text = config.get("welcome_message", "欢迎加入频道！")
    reply_markup = build_inline_keyboard(WELCOME_BTNS, row_size=BUTTON_LAYOUT["col"])
    if has_welcome_image():
        # ✅ 使用封装好的安全发送图片函数，自动处理 open + retry
        await safe_send_image(
            context.bot,
            chat_id=user.id,
            file_path=WELCOME_IMG_PATH,
            caption=welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            user_info=caption_info,
            user_id=user.id
        )
    else:
        # 无图片时发送文本欢迎信息
        await safe_send(
            context.bot,
            context.bot.send_message,
            chat_id=user.id,
            text=welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            user_info=caption_info,
            user_id=user.id
        )



# 管理员使用 /ver 指令获取机器人当前版本号
async def bot_ver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_ID):
        text = f"📌 <b>当前机器人版本：</b>\n🤖 {BOT_VER}"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# 管理员专用 /help 指令，显示所有可用管理指令说明
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    text = (
        "🛠 <b>管理员指令说明</b>\n\n"
        "<b>📥 投稿相关</b>\n"
        "/ban [用户ID] [时长(分钟)] [原因(可选)] 【禁言用户】\n"
        "/unban [用户ID] 【解除禁言】\n"
        "/banned 【查看当前禁言列表】\n"
        "/limit [on/off] [次数] 【设置每小时投稿次数限制】\n"
        "( 不带次数默认每小时30次 - 不带参数为查看当前状态 )\n\n"

        "<b>📣 自动回复设置</b>\n"
        "/setwelcome [欢迎内容] 【设置欢迎文字(支持HTML)】\n"
        "/setwelcomeimg 【设置欢迎文字附加图片】\n"
        "/clearwelcomeimg 【清除欢迎图片】\n"
        "/setautoreply [自动回复内容] 【设置自动回复消息】\n"
        "/setreplyimg 【设置自动回复附加图片】\n"
        "/clearreplyimg 【清除自动回复图片】\n"
        "/cancel 【取消操作(如取消设置欢迎图片上传指令)】\n\n"

        "<b>🔘 菜单按钮管理</b>\n"
        "/listbuttons 【查看当前按钮列表】\n"
        "/addbutton [文本] [URL] 【添加自动回复按钮】\n"
        "/editbutton [序号] [文本] [URL] 【修改自动回复按钮】\n"
        "/delbutton [序号] 【删除自动回复按钮】\n"
        "/sortbuttons [行x列(如2x2)] 【设置自动回复按钮布局】\n\n"

        "<b>📨 管理员操作</b>\n"
        "直接 <b>回复投稿</b> 可发送私信给用户\n"
        "发送 /help 查看管理员指令说明\n"
        "发送 /ver 查看当前机器人版本信息\n\n"
        
        '🤖<i>本机器人由ChatGPT协助开发</i>'
    )
    # 创建帮助菜单链接按钮
    buttons = [
        [
            InlineKeyboardButton("🌐 一只夜猫子", url="https://imneko.com"),
            InlineKeyboardButton("🔗 联系夜の猫", url="https://t.me/imnekosama")
        ]
    ]
    help_buttons = InlineKeyboardMarkup(buttons)
    #发送帮助消息 + 链接按钮
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=help_buttons)


# 设置指令菜单（仅对管理员可见）
async def setup_commands(application: Application):
    try:
        # 设置管理员专属命令菜单
        await application.bot.set_my_commands(
            commands=[
                BotCommand("ban", "禁言用户"),
                BotCommand("unban", "解除禁言"),
                BotCommand("banned", "查看禁言列表"),
                BotCommand("limit", "设置投稿频率限制"),
                BotCommand("setwelcome", "设置欢迎信息"),
                BotCommand("setwelcomeimg", "设置欢迎信息附加图片"),
                BotCommand("clearwelcomeimg", "清除欢迎信息附加图片"),
                BotCommand("setautoreply", "设置自动回复信息"),
                BotCommand("setreplyimg", "设置自动回复附加图片"),
                BotCommand("clearreplyimg", "清除自动回复附加图片"),
                BotCommand("listbuttons", "查看自动回复按钮"),
                BotCommand("addbutton", "添加自动回复按钮"),
                BotCommand("editbutton", "修改自动回复按钮"),
                BotCommand("delbutton", "删除自动回复按钮"),
                BotCommand("sortbuttons", "设置自动回复按钮布局"),
                BotCommand("cancel", "取消操作"),
                BotCommand("help", "显示帮助菜单"),
                BotCommand("ver", "显示机器人版本信息")
            ],
            scope=BotCommandScopeChat(chat_id=int(ADMIN_ID))
        )
        # 清除默认的所有人可见菜单（防止普通用户看到）
        await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
        logging.info("✅ 已设置管理员专属命令菜单，清除默认指令菜单")
    except Exception as e:
        logging.warning(f"⚠️ 设置命令菜单失败: {e}")



# 主函数：注册处理器并启动 bot（使用 polling 模式）
def main():
    # 初始化日志输出格式
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    # 创建 bot 应用实例（传入 token）
    application = Application.builder().token(TOKEN).build()
    # 设置管理员专属菜单，设置 post_init 钩子函数（事件循环准备好后自动执行）
    application.post_init = setup_commands

    # 📥 投稿处理（用户发送消息）
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.User(user_id=int(ADMIN_ID)),  # 忽略管理员的普通消息
            handle_post
        )
    )

    # 📩 管理员回复投稿用户（必须是回复文字）
    application.add_handler(
        MessageHandler(
            filters.REPLY & filters.TEXT & filters.User(user_id=int(ADMIN_ID)),
            handle_admin_reply
        )
    )

    #用户欢迎信息指令注册
    application.add_handler(CommandHandler("start", start_command))
    
    #注册管理员图片监听器
    application.add_handler(MessageHandler(filters.PHOTO & filters.User(user_id=int(ADMIN_ID)), handle_admin_image))

    # 🛠 管理指令注册
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("banned", list_banned))
    application.add_handler(CommandHandler("limit", toggle_limit))
    application.add_handler(CommandHandler("setwelcome", set_welcome))
    application.add_handler(CommandHandler("setwelcomeimg", start_set_welcome_image))
    application.add_handler(CommandHandler("clearwelcomeimg", clear_welcome_image))
    application.add_handler(CommandHandler("setautoreply", set_autoreply))
    application.add_handler(CommandHandler("setreplyimg", start_set_reply_image))
    application.add_handler(CommandHandler("clearreplyimg", clear_reply_image))
    application.add_handler(CommandHandler("sortbuttons", sort_buttons))
    application.add_handler(CommandHandler("cancel", cancel_action))
    application.add_handler(CommandHandler("help", show_help))
    application.add_handler(CommandHandler("ver", bot_ver))

    # 🔘 菜单按钮相关指令
    application.add_handler(CommandHandler("listbuttons", list_buttons))
    application.add_handler(CommandHandler("addbutton", add_button))
    application.add_handler(CommandHandler("delbutton", del_button))
    application.add_handler(CommandHandler("editbutton", edit_button))

    # 🚀 启动 bot（使用 long polling 方式，一直等待消息）
    application.run_polling()


# 启动入口：如果是主文件运行，就执行 main()
if __name__ == "__main__":
    main()

