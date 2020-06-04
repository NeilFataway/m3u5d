# m3u8d
一种m3u8播放列表快速下载合成器，合成视频需要配合ffmpeg工具使用

## 特点
* 支持进度条
* 使用协程下载所有ts片段，下载速度快
* 支持断点续传
* 支持命令行和交互式两种运行方式
* 使用ffmpeg，支持格式转化
* 支持m3u8指定头部下载
* 支持加密m3u8播放列表视频下载


## 安装方法
1. 先在本机安装ffmpeg，[下载地址](http://ffmpeg.org)
2. pip install -r requirements.txt


## 使用方法
>python m3u8d.py --help
>Usage: m3d8d [options] Arguement
>
>Options:
>  -h, --help            show this help message and exit
>  -v, --verbose         是否打印详细日志
>  -m, --merge           是否无论片段下载完全都合并ts文件
>  -f, --force           无论完整TS文件是否存在都重新下载合并
>  -o FORMAT, --output-format=FORMAT
>                        指定输出格式，默认为mp4。可选{avi|mp4}
>  -H HEADERS, --headers=HEADERS
>                       指定m3u8和ts文件下载请求中的headers
>
> SAMPLE:
> python m3u8.py http://xxxx.m3u8
> 命令结束后会在当前目录下生成xxxx.mp4（默认输出格式为mp4）
