/* Java 对照夹具：仅提供原算法所需的数据访问，不包含产品运行逻辑。 */
package com.uiloalxise;

public class ImageData
{
    private final int[] pixels;
    private final int width;
    private final int height;
    private final String format;

    /** 创建默认 PNG 数据容器。 */
    public ImageData(int[] pixels, int width, int height)
    {
        this(pixels, width, height, "png");
    }

    /** 创建带格式信息的数据容器。 */
    public ImageData(int[] pixels, int width, int height, String format)
    {
        this.pixels = pixels;
        this.width = width;
        this.height = height;
        this.format = format;
    }

    /** 读取原始 ARGB 像素。 */
    public int[] getPixels()
    {
        return pixels;
    }

    /** 读取宽度。 */
    public int getWidth()
    {
        return width;
    }

    /** 读取高度。 */
    public int getHeight()
    {
        return height;
    }

    /** 读取格式。 */
    public String getFormat()
    {
        return format;
    }
}
