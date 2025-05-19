# ✅ safe_send.py（或粘贴到 yyhime_bot.py 顶部）
# --- 用于 Telegram Bot 的安全消息发送模块 ---

import asyncio
import logging
from telegram import InputFile
from telegram.constants import ParseMode

# ✅ 安全发送函数 safe_send
# 用于替代 bot.send_message, bot.send_photo 等方法
# 自动处理异常、重试，并在所有尝试失败后通知管理员和投稿用户

# 全局错误通知缓冲区（用于聚合转发失败消息，防刷屏）
ERROR_NOTIFY_BUFFER = []
ERROR_NOTIFY_DELAY = 5  # 等待时间（秒）后批量通知
ERROR_NOTIFY_TASK = None  # 当前通知任务引用（防重复调度）

ADMIN_ID = None
# 初始化函数，让主程序主动传入 ID
def set_admin_id(admin_id):
    global ADMIN_ID
    ADMIN_ID = admin_id

# safe_send_image 函数，安全发送带图片回复信息（欢迎信息 + 自动回复）
async def safe_send_image(bot, chat_id, file_path, *, caption=None, parse_mode=None, reply_markup=None, user_info=None, user_id=None, retries=5, delay=3):
    # - 自动使用 with open 打开文件，防止重复 retry 导致句柄失效
    # - 兼容所有常用参数 + safe_send 内部自动重试
    try:
        with open(file_path, "rb") as f:
            photo = InputFile(f)
            return await safe_send(
                bot,
                bot.send_photo,
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                user_info=user_info,
                user_id=user_id,
                retries=retries,
                delay=delay
            )
    except Exception as e:
        logging.error(f"safe_send_image 错误: {e}")
        return None


# safe_send 函数，用于防止主机网络延迟卡顿导致程序崩溃
async def safe_send(bot, send_func, *args, retries=3, delay=2, user_info="未知用户", user_id=None, **kwargs):
    """
    安全发送封装函数：
    - send_func: 发送函数，如 bot.send_message、bot.send_photo 等
    - retries: 最大重试次数（默认 3 次）
    - delay: 每次重试间隔秒数（默认 2 秒）
    - user_info: 投稿人信息（用于管理员通知）
    - user_id: 投稿用户的 Telegram ID（发送失败时通知用户）
    - args/kwargs: 原始发送函数的参数
    """
    global ERROR_NOTIFY_TASK

    for attempt in range(1, retries + 1):
        try:
            return await send_func(*args, **kwargs)  # 正常执行发送函数
        except Exception as e:
            logging.warning(f"第 {attempt} 次尝试失败: {e}")

            if attempt < retries:
                await asyncio.sleep(delay)  # 重试前等待
                continue  # 继续下一次尝试
            else:
                # 最终失败，准备错误通知消息
                func_name = getattr(send_func, '__name__', str(send_func))
                error_msg = (
                    f"⚠️ <b>投稿转发失败：</b><code>{func_name}</code>\n"
                    f"👤 {user_info}\n"
                    f"❌ 错误：<code>{str(e)}</code>"
                )
                ERROR_NOTIFY_BUFFER.append(error_msg)  # 添加到缓冲区

                # 启动聚合通知任务（仅一次）
                if not ERROR_NOTIFY_TASK:
                    ERROR_NOTIFY_TASK = asyncio.create_task(send_error_notifications(bot))


async def send_error_notifications(bot):
    # 延迟聚合发送错误通知，避免刷屏
    global ERROR_NOTIFY_BUFFER, ERROR_NOTIFY_TASK
    await asyncio.sleep(ERROR_NOTIFY_DELAY)  # 等待一段时间收集错误
    if ERROR_NOTIFY_BUFFER:
        try:
            # 拼接所有错误信息
            combined_message = "\n\n".join(ERROR_NOTIFY_BUFFER)
            await bot.send_message(chat_id=ADMIN_ID, text=combined_message, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f"聚合通知发送失败: {e}")
    # 清空缓存和任务引用
    ERROR_NOTIFY_BUFFER = []
    ERROR_NOTIFY_TASK = None
