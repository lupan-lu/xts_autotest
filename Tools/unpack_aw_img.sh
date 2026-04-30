#!/bin/bash

function get_raw_data()
{
    local src=$1
    local dst=$2
    local offset=$3
    local size=$4
    local bs=4096

    local skip_num skip_rem size_num size_rem size_cur

    skip_num=$((offset/bs))
    skip_rem=$((offset%bs))

    if [ $skip_rem -ne 0 ]; then
        size_num=$(((size-bs+skip_rem)/bs))
        size_rem=$(((size-bs+skip_rem)%bs))
        dd if=$src of=$dst bs=1 skip=$offset count=$((bs-skip_rem)) status=none
        dd if=$src of=$dst bs=$bs skip=$((skip_num+1)) count=$size_num oflag=append conv=notrunc status=none
        if [ $size_rem -ne 0 ]; then
            dd if=$src of=$dst bs=1 skip=$(((skip_num+1+size_num)*bs)) count=$size_rem oflag=append conv=notrunc status=none
        fi
    else
        size_num=$((size/bs))
        size_rem=$((size%bs))
        dd if=$src of=$dst bs=$bs skip=$skip_num count=$size_num status=none
        if [ $size_rem -ne 0 ]; then
            dd if=$src of=$dst bs=1 skip=$(((skip_num+size_num)*bs)) count=$size_rem oflag=append conv=notrunc status=none
        fi
    fi
}

function extract_data()
{
    local fw=$1
    local out=$2
    local magic="0001000000040000"
    local offset main sub name size ofs newname

    for ((i=0;i<256;i++)); do
        offset=$((i*1024))
        if [ "x$(hexdump -s $offset -n 8 -v -e '8/1 "%02X"' $fw)" == "x$magic" ]; then
            main=$(hexdump -s $((offset+8)) -n 8 -v -e '"%c"' $fw)
            sub=$(hexdump -s $((offset+16)) -n 16 -v -e '"%c"' $fw)
            name=$(hexdump -s $((offset+36)) -n 32 -v -e '"%c"' $fw | tr -d '\000')
            size=$(hexdump -s $((offset+300)) -n 4 -v -e '4/1 "%02X"' $fw)
            size=$(printf "%d" 0x${size:6:2}${size:4:2}${size:2:2}${size:0:2})
            ofs=$(hexdump -s $((offset+308)) -n 4 -v -e '4/1 "%02X"' $fw)
            ofs=$(printf "%d" 0x${ofs:6:2}${ofs:4:2}${ofs:2:2}${ofs:0:2})

            newname=$name
            printf "[%-8s, %-16s]: %-18s, offset: %08X, size: %08X\n" "$main" "$sub" "$name" $ofs $size
            case "$main" in
                RFSFAT16)
                    if [ $size -gt 4 ]; then
                        [ "$name" != "env.fex" -a "$name" != "boot-resource.fex" ] && newname=${name/%.fex/.img}
                        get_raw_data "$fw" "$out/${newname}" $ofs $size
                    fi
                    ;;
                *)
                    if [ "$name" == "sunxi_mbr.fex" ] || [ "$name" == "toc0.fex" ] || [ "$name" == "toc0_ufs.fex" ] || [ "$name" == "toc1.fex" ] || [ "$name" == "boot0_sdcard.fex" ] || [ "$name" == "boot0_nand.fex" ] || [ "$name" == "u-boot.fex" ] || [ "$name" == "boot_package.fex" ]; then
                        get_raw_data "$fw" "$out/${newname}" $ofs $size
                    fi
                    ;;
            esac
        fi
    done
}

function unpack_img()

{
    local fw=$1
    local out=$2

    mkdir -p $out
    find $out -type f | xargs rm -rf

    extract_data $fw $out
}

[ -z "$1" ] && echo "args1: firmware name" && exit 0

imgname=$1
outdir=$(cd $(dirname $0) && pwd)/output

unpack_img $imgname $outdir
