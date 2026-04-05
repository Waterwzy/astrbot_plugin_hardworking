import asyncio
import json
import shutil
import traceback

import pendulum

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools


class FileFormatError(Exception):
    pass


class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.hardwork_list = {}
        self._hd_lock = asyncio.Lock()

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        self.check_data_format()

    def check_data_format(self):
        file_root_path = StarTools.get_data_dir()
        if not file_root_path.exists():
            file_root_path.mkdir(parents=True)

        default_list = {"hardwork_user": {}}

        file_path = file_root_path / "hardwork.json"
        if not file_path.exists():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(default_list, f, ensure_ascii=False, indent=4)

        try:
            with open(file_path, encoding="utf-8") as f:
                self.hardwork_list = json.load(f)
                if (
                    not isinstance(self.hardwork_list, dict)
                    or "hardwork_user" not in self.hardwork_list
                    or not isinstance(self.hardwork_list["hardwork_user"], dict)
                ):
                    raise FileFormatError
        except (json.JSONDecodeError, FileFormatError):
            logger.error("文件数据损坏，正在备份并创建新文件...")
            backup_path = (
                file_root_path / f"list_backup{int(pendulum.now().timestamp())}"
            )
            try:
                shutil.copy(file_path, backup_path)
                logger.info(f"损坏文件已备份至{backup_path}")
            except Exception:
                trace_err = traceback.format_exc()
                logger.error(f"备份文件创建失败:{trace_err}")

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(default_list, f, ensure_ascii=False, indent=4)
            logger.info("已重新创建默认文件")
            self.hardwork_list = default_list
        except Exception:
            trace_err = traceback.format_exc()
            logger.error(f"读取文件出现未知异常{trace_err}")
            self.hardwork_list = default_list
        return

    def check_time_format(self, times: str) -> MessageChain | None:
        try:
            work_time = pendulum.parse(times)
            if not isinstance(work_time, pendulum.Duration):
                chain = MessageChain().message(
                    "这不是一个符合ISO8601规范的持续时间，请核实后再试。"
                )
                return chain
            if work_time.in_seconds() <= 0:
                chain = MessageChain().message("设置时间不能为负数。")
                return chain
        except pendulum.parsing.exceptions.ParserError:
            chain = MessageChain().message(
                "这不是一个符合ISO8601规范的持续时间，请核实后再试。"
            )
            return chain
        return None

    def write_list(self, work_list):
        data_dir = StarTools.get_data_dir()
        if not data_dir.exists():
            data_dir.mkdir(parents=True)
        file_path = data_dir / "hardwork.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(work_list, f, ensure_ascii=False, indent=4)
        except Exception:
            trace_err = traceback.format_exc()
            logger.error(f"写入文件失败{trace_err}")
        return

    def clear_task(self):
        flag = False
        for plat in self.hardwork_list["hardwork_user"].keys():
            for user_item in list(self.hardwork_list["hardwork_user"][plat].keys()):
                if (
                    self.hardwork_list["hardwork_user"][plat][user_item].get(
                        "end_time", 0
                    )
                    <= pendulum.now().timestamp()
                ):
                    self.hardwork_list["hardwork_user"][plat].pop(user_item)
                    flag = True
        if flag:
            self.write_list(self.hardwork_list)
        return

    def create_work(
        self, times: pendulum.Duration, plat_name: str, user_id: str, forced: bool
    ) -> tuple[str, str]:
        if self.hardwork_list["hardwork_user"].get(plat_name, None) is None:
            self.hardwork_list["hardwork_user"][plat_name] = {}

        self.clear_task()

        if self.hardwork_list["hardwork_user"][plat_name].get(
            user_id
        ) is not None and self.hardwork_list["hardwork_user"][plat_name][user_id].get(
            "forced", False
        ):
            return "Fail", "你已经设置了强制专注时间；结束之前无法取消或重新设置"

        future = pendulum.now() + times

        hardwork_item = {
            "end_time": future.timestamp(),
            "forced": forced,
        }

        self.hardwork_list["hardwork_user"][plat_name][user_id] = hardwork_item
        self.write_list(self.hardwork_list)
        return "Success", future.strftime("%Y年%m月%d日 %H:%M:%S")

    @filter.command_group("hd")
    def hd(self):
        """hardworking插件指令组注册"""
        pass

    @hd.command("set")
    async def hd_set(self, event: AstrMessageEvent, times: str):
        """设置可以解除的专注时间"""
        async with self._hd_lock:
            chain = self.check_time_format(times)
            if chain is None:
                event.stop_event()
                times = pendulum.parse(times)
                status, detail = self.create_work(
                    times, event.platform_meta.name, event.get_sender_id(), False
                )
                if status == "Fail":
                    chain = MessageChain().message(detail)
                else:
                    chain = MessageChain().message(
                        f"设置专注时间成功，专注时间持续到{detail}"
                    )

        await event.send(chain)

    @hd.command("fset")
    async def hd_fset(self, event: AstrMessageEvent, times: str):
        """设置不可解除的专注时间"""
        async with self._hd_lock:
            chain = self.check_time_format(times)
            if chain is None:
                event.stop_event()
                times = pendulum.parse(times)
                status, detail = self.create_work(
                    times, event.platform_meta.name, event.get_sender_id(), True
                )
                if status == "Fail":
                    chain = MessageChain().message(detail)
                else:
                    chain = MessageChain().message(
                        f"设置强制专注时间成功，专注时间持续到{detail}"
                    )

        await event.send(chain)

    @hd.command("clear")
    async def clear(self, event: AstrMessageEvent):
        """解除可以取消的专注时间"""
        plat_name = event.platform_meta.name
        sender = event.get_sender_id()
        async with self._hd_lock:
            event.stop_event()
            self.clear_task()
            if (
                self.hardwork_list["hardwork_user"].get(plat_name) is not None
                and self.hardwork_list["hardwork_user"][plat_name].get(sender)
                is not None
            ):
                if not self.hardwork_list["hardwork_user"][plat_name][sender]["forced"]:
                    self.hardwork_list["hardwork_user"][plat_name].pop(sender)
                    self.write_list(self.hardwork_list)
                    chain = MessageChain().message("解除专注时间成功！")
                else:
                    chain = MessageChain().message(
                        self.config["force_hardwork_decorate"]["force_notify"]
                    )
            else:
                chain = MessageChain().message("你还没有设置专注时间")

        await event.send(chain)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def check_hardwork(self, event: AstrMessageEvent):
        """检查用户是否处于专注时间"""
        plat_name = event.platform_meta.name
        sender_id = event.get_sender_id()
        async with self._hd_lock:
            self.clear_task()
            chain = None
            if (
                self.hardwork_list["hardwork_user"].get(plat_name) is not None
                and sender_id in self.hardwork_list["hardwork_user"][plat_name]
            ):
                times_str = pendulum.from_timestamp(
                    self.hardwork_list["hardwork_user"][plat_name][sender_id][
                        "end_time"
                    ]
                ).strftime("%Y年%m月%d日 %H:%M:%S")
                if self.hardwork_list["hardwork_user"][plat_name][sender_id]["forced"]:
                    fin_str = (
                        self.config["force_hardwork_decorate"]["force_hardwork_prefix"]
                        + times_str
                        + self.config["force_hardwork_decorate"][
                            "force_hardwork_suffix"
                        ]
                    )
                else:
                    fin_str = (
                        self.config["hardwork_decorate"]["hardwork_prefix"]
                        + times_str
                        + self.config["hardwork_decorate"]["hardwork_suffix"]
                    )
                chain = MessageChain().message(fin_str)
                event.stop_event()
        if chain:
            await event.send(chain)

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
