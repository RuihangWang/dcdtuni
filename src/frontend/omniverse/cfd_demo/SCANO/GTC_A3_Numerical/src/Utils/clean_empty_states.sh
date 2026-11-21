#!/bin/bash

# 可配置的清理路径，默认为空，必须为绝对路径（使用当前目录）
# Dev utility, unrelated to the demo. The default is one machine's layout; pass
# CLEAN_PATH to point it anywhere else.
clean_path="${CLEAN_PATH:-$HOME/Logger}"

# 可配置的保留条件列表
# 格式说明：
# - 以"/"结尾表示检查文件夹，如"states/"表示检查是否存在名为states的文件夹
# - 包含"*"表示文件通配符，如"*.vtu"表示检查是否存在任意.vtu文件
# - 普通字符串表示检查具体文件名，如"config.txt"表示检查是否存在config.txt文件
# 只要满足列表中任意一个条件，该时间文件夹就会被保留
keep_conditions=(
    "states/"
    "*.vtu"
)

# 脚本用于清理Logger目录下不匹配保留条件的日期命名文件夹
# 用法：
# 先运行试运行模式查看将要删除的内容： ./clean_empty_states.sh --dry-run
# 如果结果符合预期，再运行交互模式： ./clean_empty_states.sh
# 如果确信无误，可以使用自动模式： ./clean_empty_states.sh --auto
#
# 自定义清理路径：
# 在脚本开头修改 clean_path 变量，例如：clean_path="/path/to/your/directory"

# 设置颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志文件
# LOG_FILE="clean_empty_states_$(date +%Y%m%d_%H%M%S).log"
LOG_FILE="/dev/null"

# 函数：打印带颜色的消息
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ${message}" >> "$LOG_FILE"
}

# 函数：检查目录是否为日期格式 (YYYY-MM-DD-HH-MM-SS)
is_date_format() {
    local dirname=$1
    # 匹配格式：2025-06-24-12-49-09
    if [[ $dirname =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]]; then
        return 0
    else
        return 1
    fi
}

# 函数：检查单个条件是否满足
check_condition() {
    local date_dir=$1
    local condition=$2

    # 检查是否为文件夹条件（以/结尾）
    if [[ "$condition" == *"/" ]]; then
        local folder_name=${condition%/}  # 移除末尾的斜杠
        if [ -d "$date_dir/$folder_name" ]; then
            return 0  # 文件夹存在
        fi
    # 检查是否为文件通配符条件（包含*）
    elif [[ "$condition" == *"*"* ]]; then
        # 递归查找匹配的文件
        if find "$date_dir" -name "$condition" -type f 2>/dev/null | grep -q .; then
            return 0  # 找到匹配的文件
        fi
    # 检查具体文件名
    else
        if [ -f "$date_dir/$condition" ]; then
            return 0  # 文件存在
        fi
    fi

    return 1  # 条件不满足
}

# 函数：检查目录是否应该保留（基于配置的条件列表）
should_keep_directory() {
    local date_dir=$1

    # 遍历所有保留条件
    for condition in "${keep_conditions[@]}"; do
        if check_condition "$date_dir" "$condition"; then
            echo "$condition"
            return 0
        fi
    done

    echo "none"
    return 1
}

# 主函数
main() {
    # 确定工作目录
    if [ -n "$clean_path" ]; then
        if [ -d "$clean_path" ]; then
            CURRENT_DIR=$(realpath "$clean_path")
            cd "$CURRENT_DIR" || {
                print_message "$RED" "错误: 无法进入指定目录 $clean_path"
                exit 1
            }
            print_message "$BLUE" "开始清理指定目录下不匹配保留条件的日期命名文件夹..."
        else
            print_message "$RED" "错误: 指定的路径不存在或不是目录: $clean_path"
            exit 1
        fi
    else
        CURRENT_DIR=$(pwd)
        print_message "$BLUE" "开始清理当前目录下不匹配保留条件的日期命名文件夹..."
    fi

    print_message "$BLUE" "工作目录: $CURRENT_DIR"
    
    # 统计变量
    total_checked=0
    deleted_count=0
    kept_count=0
    
    # 遍历所有子目录
    for net_dir in */; do
        if [ -d "$net_dir" ]; then
            print_message "$BLUE" "检查网络目录: $net_dir"
            
            # 进入网络目录 (如 "net TransFVGN_v2; hs 128;/")
            cd "$net_dir" || continue
            
            # 遍历日期命名的文件夹
            for date_dir in */; do
                if [ -d "$date_dir" ]; then
                    # 移除末尾的斜杠
                    date_dirname=${date_dir%/}
                    
                    # 检查是否为日期格式
                    if is_date_format "$date_dirname"; then
                        total_checked=$((total_checked + 1))
                        print_message "$YELLOW" "检查日期文件夹: $date_dirname"
                        
                        # 检查是否应该保留（基于配置的条件列表）
                        keep_reason=$(should_keep_directory "$date_dir")
                        if [ $? -eq 0 ]; then
                            print_message "$GREEN" "  ✓ 保留 $date_dirname (匹配条件: $keep_reason)"
                            kept_count=$((kept_count + 1))
                        else
                            print_message "$RED" "  ✗ 删除 $date_dirname (不匹配任何保留条件)"
                            
                            # 询问用户确认（可选，如果需要自动删除请注释掉这部分）
                            read -p "确认删除 $date_dirname 吗? (y/N): " confirm
                            if [[ $confirm =~ ^[Yy]$ ]]; then
                                rm -rf "$date_dir"
                                if [ $? -eq 0 ]; then
                                    print_message "$GREEN" "    已成功删除 $date_dirname"
                                    deleted_count=$((deleted_count + 1))
                                else
                                    print_message "$RED" "    删除 $date_dirname 失败"
                                fi
                            else
                                print_message "$YELLOW" "    跳过删除 $date_dirname"
                                kept_count=$((kept_count + 1))
                            fi
                        fi
                    else
                        print_message "$YELLOW" "  跳过非日期格式文件夹: $date_dirname"
                    fi
                fi
            done
            
            # 返回上级目录
            cd ..
        fi
    done

    # 打印统计信息
    print_message "$BLUE" "==================== 清理完成 ===================="
    print_message "$BLUE" "总共检查的日期文件夹: $total_checked"
    print_message "$GREEN" "保留的文件夹: $kept_count"
    print_message "$RED" "删除的文件夹: $deleted_count"
    print_message "$BLUE" "日志文件: $LOG_FILE"
    print_message "$BLUE" "=================================================="
}

# 自动删除版本（不需要用户确认）
auto_clean() {
    # 确定工作目录
    if [ -n "$clean_path" ]; then
        if [ -d "$clean_path" ]; then
            CURRENT_DIR=$(realpath "$clean_path")
            cd "$CURRENT_DIR" || {
                print_message "$RED" "错误: 无法进入指定目录 $clean_path"
                exit 1
            }
            print_message "$BLUE" "开始自动清理指定目录下不匹配保留条件的日期命名文件夹..."
        else
            print_message "$RED" "错误: 指定的路径不存在或不是目录: $clean_path"
            exit 1
        fi
    else
        CURRENT_DIR=$(pwd)
        print_message "$BLUE" "开始自动清理当前目录下不匹配保留条件的日期命名文件夹..."
    fi

    print_message "$RED" "警告: 自动模式将直接删除文件夹，无需确认！"
    print_message "$BLUE" "工作目录: $CURRENT_DIR"
    
    # 统计变量
    total_checked=0
    deleted_count=0
    kept_count=0
    
    # 遍历所有子目录
    for net_dir in */; do
        if [ -d "$net_dir" ]; then
            print_message "$BLUE" "检查网络目录: $net_dir"
            
            # 进入网络目录
            cd "$net_dir" || continue
            
            # 遍历日期命名的文件夹
            for date_dir in */; do
                if [ -d "$date_dir" ]; then
                    # 移除末尾的斜杠
                    date_dirname=${date_dir%/}
                    
                    # 检查是否为日期格式
                    if is_date_format "$date_dirname"; then
                        total_checked=$((total_checked + 1))
                        
                        # 检查是否应该保留（基于配置的条件列表）
                        keep_reason=$(should_keep_directory "$date_dir")
                        if [ $? -eq 0 ]; then
                            print_message "$GREEN" "  ✓ 保留 $date_dirname (匹配条件: $keep_reason)"
                            kept_count=$((kept_count + 1))
                        else
                            print_message "$RED" "  ✗ 删除 $date_dirname (不匹配任何保留条件)"
                            rm -rf "$date_dir"
                            if [ $? -eq 0 ]; then
                                print_message "$GREEN" "    已成功删除 $date_dirname"
                                deleted_count=$((deleted_count + 1))
                            else
                                print_message "$RED" "    删除 $date_dirname 失败"
                            fi
                        fi
                    fi
                fi
            done
            
            # 返回上级目录
            cd ..
        fi
    done

    # 打印统计信息
    print_message "$BLUE" "==================== 清理完成 ===================="
    print_message "$BLUE" "总共检查的日期文件夹: $total_checked"
    print_message "$GREEN" "保留的文件夹: $kept_count"
    print_message "$RED" "删除的文件夹: $deleted_count"
    print_message "$BLUE" "日志文件: $LOG_FILE"
    print_message "$BLUE" "=================================================="
}

# 显示帮助信息
show_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help     显示此帮助信息"
    echo "  -a, --auto     自动模式，不需要用户确认直接删除"
    echo "  -d, --dry-run  试运行模式，只显示将要删除的文件夹，不实际删除"
    echo ""
    echo "描述:"
    echo "  此脚本用于清理指定目录或当前目录下不匹配保留条件的日期命名文件夹。"
    echo "  可以通过修改脚本开头的 clean_path 变量来指定要清理的目录路径。"
    echo "  可以通过修改脚本开头的 keep_conditions 数组来配置保留条件。"
    echo "  如果 clean_path 为空，则使用当前目录。"
    echo "  日期格式: YYYY-MM-DD-HH-MM-SS (例如: 2025-06-24-12-49-09)"
    echo ""
    echo "配置:"
    echo "  在脚本开头设置 clean_path 变量来指定要清理的目录："
    echo "  clean_path=\"\"                    # 使用当前目录（默认）"
    echo "  clean_path=\"/path/to/directory\"   # 使用指定目录"
    echo ""
    echo "  在脚本开头设置 keep_conditions 数组来配置保留条件："
    echo "  keep_conditions=(\"states/\" \"*.vtu\" \"config.txt\")"
    echo "  - 以\"/\"结尾表示检查文件夹，如\"states/\""
    echo "  - 包含\"*\"表示文件通配符，如\"*.vtu\""
    echo "  - 普通字符串表示检查具体文件，如\"config.txt\""
    echo ""
    echo "示例:"
    echo "  $0              # 交互模式，每个删除操作都需要确认"
    echo "  $0 --auto       # 自动模式，直接删除所有符合条件的文件夹"
    echo "  $0 --dry-run    # 试运行，只显示将要删除的文件夹"
}

# 试运行模式
dry_run() {
    print_message "$BLUE" "试运行模式: 显示将要删除的文件夹（不实际删除）"

    # 确定工作目录
    if [ -n "$clean_path" ]; then
        if [ -d "$clean_path" ]; then
            CURRENT_DIR=$(realpath "$clean_path")
            cd "$CURRENT_DIR" || {
                print_message "$RED" "错误: 无法进入指定目录 $clean_path"
                exit 1
            }
        else
            print_message "$RED" "错误: 指定的路径不存在或不是目录: $clean_path"
            exit 1
        fi
    else
        CURRENT_DIR=$(pwd)
    fi

    print_message "$BLUE" "工作目录: $CURRENT_DIR"
    
    # 统计变量
    total_checked=0
    will_delete=0
    will_keep=0
    
    # 遍历所有子目录
    for net_dir in */; do
        if [ -d "$net_dir" ]; then
            print_message "$BLUE" "检查网络目录: $net_dir"
            
            # 进入网络目录
            cd "$net_dir" || continue
            
            # 遍历日期命名的文件夹
            for date_dir in */; do
                if [ -d "$date_dir" ]; then
                    # 移除末尾的斜杠
                    date_dirname=${date_dir%/}
                    
                    # 检查是否为日期格式
                    if is_date_format "$date_dirname"; then
                        total_checked=$((total_checked + 1))
                        
                        # 检查是否应该保留（基于配置的条件列表）
                        keep_reason=$(should_keep_directory "$date_dir")
                        if [ $? -eq 0 ]; then
                            print_message "$GREEN" "  ✓ 将保留 $date_dirname (匹配条件: $keep_reason)"
                            will_keep=$((will_keep + 1))
                        else
                            print_message "$RED" "  ✗ 将删除 $date_dirname (不匹配任何保留条件)"
                            will_delete=$((will_delete + 1))
                        fi
                    fi
                fi
            done
            
            # 返回上级目录
            cd ..
        fi
    done

    # 打印统计信息
    print_message "$BLUE" "==================== 试运行结果 ===================="
    print_message "$BLUE" "总共检查的日期文件夹: $total_checked"
    print_message "$GREEN" "将保留的文件夹: $will_keep"
    print_message "$RED" "将删除的文件夹: $will_delete"
    print_message "$BLUE" "=================================================="
}

# 解析命令行参数
case "${1:-}" in
    -h|--help)
        show_help
        exit 0
        ;;
    -a|--auto)
        auto_clean
        ;;
    -d|--dry-run)
        dry_run
        ;;
    "")
        main
        ;;
    *)
        echo "未知选项: $1"
        echo "使用 $0 --help 查看帮助信息"
        exit 1
        ;;
esac
