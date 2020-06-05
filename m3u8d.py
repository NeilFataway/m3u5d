#!/usr/bin/env python
# coding:utf8
# 支持断点续传
from __future__ import division

import sys
import os
import requests
import shutil
import time
import logging
import re
# import ffmpeg
import subprocess

from subprocess import CalledProcessError
from gevent import monkey, pool
from progressbar import Percentage, Bar, Timer, ETA, ProgressBar
from optparse import OptionParser
from StringIO import StringIO
from urlparse import urlparse, urljoin
from collections import OrderedDict
from Crypto.Cipher import AES
from binascii import a2b_hex

# import multiprocessing

monkey.patch_all()

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))


codecs = {}


class Codec(object):
    def __init__(self, method, uri="", iv_str=""):
        if method == "AES-128":
            self.mode = AES.MODE_CBC
        else:
            raise NotImplemented("暂不支持该加密方法的解码")

        if iv_str:
            if iv_str.startswith("0x"):
                iv_str = iv_str[2:]
            self.iv = a2b_hex(iv_str)
        else:
            self.iv = None

        if uri:
            try:
                rsp = requests.get(uri)
                if rsp.status_code == 200:
                    self.key = rsp.content
                else:
                    raise Exception("下载解密key失败，错误码返回码：%s" % rsp.status_code)
            except Exception as e:
                raise Exception("下载解密key失败, 错误异常：%s" % e.message)
        else:
            raise Exception("未指定解密key uri")

        self.cryptor = AES.new(self.key, self.mode, self.iv)

    def decode(self, data):
        return self.cryptor.decrypt(data)


class FileMerger(object):
    def __init__(self, path, format="ts"):
        self.video_name = os.path.basename(path)
        self.video_file = "{}.{}".format(self.video_name, format)
        self.format = format

    def run(self):
        return self.merge()

    def merge(self, remove_src=True):
        ts_list = [os.path.join(self.video_name, i) for i in os.listdir(self.video_name) if i.endswith(".ts")]
        ts_list.sort()
        # ff_input_list = [ffmpeg.input(os.path.join(self.video_name, ts)) for ts in ts_list]
        # (
        #     ffmpeg.concat(*ff_input_list)
        #     .output(self.video_file)
        #     .run()
        # )
        if self.format == "ts":
            with open(self.video_file, "w") as f:
                for ts in ts_list:
                    f.write(open(ts).read())
        else:
            concat_arg = "concat:" + "|".join(ts_list)

            cmdlist = [
                "ffmpeg",
                "-i",
                concat_arg,
                self.video_file]
            devNull = open(os.devnull, "w")
            subprocess.check_call(cmdlist, stdout=devNull, stderr=devNull)

        if remove_src:
            shutil.rmtree(self.video_name)


class FileDownloader(pool.Pool, FileMerger):
    def __init__(self, m3u8_url, format="mp4", merge=False, force=False, headers=None, pool_size=8):
        self.m3u8_url = m3u8_url
        self.video_name = urlparse(m3u8_url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        self.format = format
        self.video_file = "{}.{}".format(self.video_name, format)
        self.force = force
        self.merge_flag = merge
        self.base_url = ""
        self.ts_url = OrderedDict()
        self.url_done_list = []
        self.slice_num = 0
        self.slice_done_num = 0
        self.headers = {header.split(":", 1)[0]:header.split(":", 1)[1] for header in headers} if headers else None
        self.parse_m3u8()
        pool.Pool.__init__(self, pool_size)
        FileMerger.__init__(self, self.video_name, format=self.format)

        # Progress bar
        widgets = ['视频{}下载'.format(self.video_name),
                   Percentage(), ' ',
                   Bar('#'), ' ']
        self.bar = ProgressBar(widgets=widgets, maxval=self.slice_num)

    @property
    def done_percent(self):
        return (self.slice_done_num / self.slice_num) * 100

    @property
    def url2download(self):
        return list(set(self.ts_url.keys()) - set(self.url_done_list))

    def parse_m3u8(self, retry=0):
        if retry >= 3:
            logger.error("视频{}索引文件下载失败".format(self.video_name))
            raise Exception()
        try:
            if self.headers:
                m3u8 = requests.get(self.m3u8_url, headers=self.headers)
            else:
                m3u8 = requests.get(self.m3u8_url)
        except:
            retry += 1
            return self.parse_m3u8(retry)

        m3u8_content_lines = StringIO(m3u8.content).readlines()
        cur_ext_x_codec = None
        multi_level_m3u_flag = False
        for line in m3u8_content_lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("#"):
                if line.startswith("#EXT-X-KEY"):
                    ext_x_key = line.split(":", 1)[1]
                    ext_x_map = {
                        item.split("=", 1)[0]: item.split("=", 1)[1] for item in ext_x_key.split(",")
                    }
                    method = ext_x_map.get("METHOD")
                    if method and method != "NONE":
                        uri, iv = ("",) * 2
                        if ext_x_map.get("URI"):
                            uri = ext_x_map.get("URI").strip('"') or None
                        if ext_x_map.get("IV"):
                            iv = ext_x_map.get("IV") or None
                        if uri:
                            if not validate_url(uri):
                                uri = urljoin(self.m3u8_url, uri)
                        if (method, uri, iv) not in codecs:
                            cur_ext_x_codec = Codec(method, uri, iv)
                            codecs[(method, uri, iv)] = cur_ext_x_codec
                        else:
                            cur_ext_x_codec = codecs[(method, uri, iv)]

                if line.startswith("EXT-X-STREAM-INF"):
                    multi_level_m3u_flag = True

            else:
                if multi_level_m3u_flag:
                    # 两层m3u8，可以选择不同码率。默认下载第一种码率
                    # 清空ts_url
                    self.ts_url = OrderedDict()
                    self.m3u8_url = urljoin(self.m3u8_url, line)
                    return self.parse_m3u8()

                self.ts_url[urljoin(self.m3u8_url, line)] = cur_ext_x_codec

        if os.path.isdir(self.video_name):
            done_index_list = [int(i.split(".ts")[0]) for i in os.listdir(self.video_name) if i.endswith(".ts")]
            self.url_done_list = [self.ts_url.keys()[index] for index in done_index_list]
            self.slice_num = len(self.ts_url)
            self.slice_done_num = len(self.url_done_list)
            logger. info("视频%s已完成下载%.2f%%, 继续下载.\n" % (self.video_name, self.done_percent))
        else:
            self.url_done_list = []
            self.slice_num = len(self.ts_url)
            self.slice_done_num = len(self.url_done_list)

    def run(self):
        if os.path.isfile(self.video_file) and not self.force:
            logger.info("视频文件{}已存在，下载操作取消.\n".format(self.video_file))
            return
        self.bar.start()
        self.bar.update(self.slice_done_num)

        for ts_url in self.url2download:
            self.spawn(self.download_ts, ts_url)
        self.join()
        if self.slice_done_num < self.slice_num:
            if self.merge_flag:
                logging.warn("视频%s部分片段下载失败，完成%.2f%%。警告：在此情况下合并的TS文件会有片段丢失。" %
                             (self.video_name, self.done_percent))
                logger.debug("下载失败片段：\n{}\n".format("\n".join(self.url2download)))
                return self.merge(remove_src=False)

            else:
                logger.info("视频%s部分片段下载失败，完成%.2f%%。下次运行将触发断点续传。\n" %
                            (self.video_name, self.done_percent))

                logger.debug("下载失败片段：\n{}\n".format("\n".join(self.url2download)))
                return 1
        self.bar.finish()
        logger.info("视频%s下载完成，转码中..." % self.video_name)
        try:
            self.merge()
        except CalledProcessError as e:
            logger.error("视频解码失败")
            return 1
        logger.info("转码完成，视频文件%s已生成" % self.video_file)
        return 0

    def download_ts(self, ts_url, retry=0):
        index = self.ts_url.keys().index(ts_url)
        if retry >= 10:
            return 1

        try:
            if self.headers:
                r = requests.get(ts_url, headers=self.headers)
            else:
                r = requests.get(ts_url)
        except Exception:
            time.sleep(1)
            retry += 1
            return self.download_ts(ts_url, retry)

        if not os.path.isdir(self.video_name):
            os.mkdir(self.video_name)

        with open(os.path.join(self.video_name, "{:08d}.ts".format(index)), "wb") as fp:
            codcs = self.ts_url.get(ts_url)
            if codcs:
                fp.write(codcs.decode(r.content))
            else:
                fp.write(r.content)

        self.url_done_list.append(ts_url)
        self.slice_done_num += 1
        self.bar.update(self.slice_done_num)


def shell(format="mp4", merge=False, force=False, headers=None):
    # pool = multiprocessing.Pool(processes=3)
    m3u8_url = raw_input("输入索引文件url/ts路径(输入Q退出)：")

    while True:
        if m3u8_url.strip() == "Q":
            return 1
        if validate_url(m3u8_url):
            return download_video(m3u8_url, format, merge, force, headers)
        elif os.path.exists(m3u8_url.strip()):
            return merge_video(m3u8_url, format)
        else:
            m3u8_url = raw_input("无效索引文件url/TS路径，请重新输入(输入Q退出): ")

    # pool.close()
    # pool.join()


def validate_url(url):
    if re.match(r'^https?:/{2}\w.+$', url):
        return True
    else:
        return False


def download_video(m3u8_url, format="mp4", merge=False, force=False, headers=None):
    if not validate_url(m3u8_url):
        logger.error("无效索引文件url")
        return 1
    downloader = FileDownloader(m3u8_url, format, merge, force, headers)
    return downloader.run()


def merge_video(ts_path, format="mp4"):
    if not os.path.exists(ts_path):
        logger.error("无效目录")

    merger = FileMerger(ts_path, format=format)
    merger.run()

# def headersOptCallback(option, opt_str, value, parser):
#     dest = getattr(parser.values, option.dest)
#     key, val = value.split("：", 1)
#     if dest:
#         dest[key] = val
#         setattr(parser.values, option.dest, dest
#     else:
#         dest = {key: val}
#     )


if __name__ == "__main__":
    # multiprocessing.freeze_support()
    parser = OptionParser()
    parser.add_option("-v", "--verbose", action="store_true", dest="verbose", default=False,
                      help="是否打印详细日志".decode("utf8"))

    parser.add_option("-m", "--merge", action="store_true", dest="merge", default=False,
                      help="是否无论片段下载完全都合并ts文件".decode("utf8"))

    parser.add_option("-f", "--force", action="store_true", dest="force", default=False,
                      help="无论完整TS文件是否存在都重新下载合并".decode("utf8"))

    parser.add_option("-o", "--output-format", action="store", dest="format", default="mp4",
                      help="指定输出格式，默认为mp4。可选{avi|mp4}".decode("utf8"))

    parser.add_option("-H", "--headers", action="append", dest="headers", #callback=headersOptCallback,
                      help="指定m3u8和ts文件下载请求中的headers".decode("utf8"))

    options, args = parser.parse_args()

    if options.verbose:
        logger.setLevel("DEBUG")
    else:
        logger.setLevel("INFO")

    if len(args) == 0:
        exit(shell(format=options.format, merge=options.merge, force=options.force, headers=options.headers))

    elif len(args) == 1:
        if validate_url(args[0]):
            exit(download_video(args[0],
                                format=options.format,
                                merge=options.merge,
                                force=options.force,
                                headers=options.headers))
        elif os.path.exists(args[0].strip()):
            exit(merge_video(args[0], format=options.format))

    else:
        parser.print_help()
        exit(1)
