/* Java 对照入口：直接调用固定版本原算法，生成 PNG 并验证 Python 图片互通。 */
import com.uiloalxise.ImageData;
import com.uiloalxise.Util;
import java.awt.image.BufferedImage;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import javax.imageio.ImageIO;

public class ReferenceMain
{
    /** 将测试像素原样写为带透明通道的 PNG，不主动丢弃隐藏 RGB。 */
    private static void save(ImageData data, File output) throws Exception
    {
        BufferedImage image = new BufferedImage(data.getWidth(), data.getHeight(), BufferedImage.TYPE_INT_ARGB);
        image.setRGB(0, 0, data.getWidth(), data.getHeight(), data.getPixels(), 0, data.getWidth());
        if (!ImageIO.write(image, "png", output))
        {
            throw new IllegalStateException("PNG 写入失败");
        }
    }

    /** 调用原项目的公开工具接口，不重写算法或密钥规则。 */
    private static ImageData process(String algorithm, String key, ImageData image, boolean inverse)
    {
        switch (algorithm)
        {
            case "tomato": return inverse ? Util.tomatoDecrypt(key, image) : Util.tomatoEncrypt(key, image);
            case "block": return inverse ? Util.blockDecrypt(key, image) : Util.blockEncrypt(key, image);
            case "row-pixel": return inverse ? Util.rowPixelDecrypt(key, image) : Util.rowPixelEncrypt(key, image);
            case "per-pixel": return inverse ? Util.perPixelDecrypt(key, image) : Util.perPixelEncrypt(key, image);
            case "pic-row": return inverse ? Util.picEncryptRowDecrypt(key, image) : Util.picEncryptRowEncrypt(key, image);
            case "pic-row-column": return inverse ? Util.picEncryptRowColumnDecrypt(key, image) : Util.picEncryptRowColumnEncrypt(key, image);
            case "sort": return inverse ? Util.sortDecrypt(image) : Util.sortEncrypt(image);
            case "random": return inverse ? Util.randomDecrypt(key, image) : Util.randomEncrypt(key, image);
            default: throw new IllegalArgumentException("未知算法");
        }
    }

    /** 生成测试向量，或用原算法解混淆由 Python 写出的 PNG。 */
    public static void main(String[] args) throws Exception
    {
        // 修复 Windows 启动参数对非系统字符的替换：通过 ASCII Base64 传入真实 UTF-8 密钥。
        String key = new String(Base64.getDecoder().decode(args[2]), StandardCharsets.UTF_8);
        if (args[0].equals("decrypt"))
        {
            BufferedImage input = ImageIO.read(new File(args[3]));
            int width = input.getWidth();
            int height = input.getHeight();
            int[] pixels = input.getRGB(0, 0, width, height, null, 0, width);
            save(process(args[1], key, new ImageData(pixels, width, height), true), new File(args[4]));
            return;
        }
        String algorithm = args[1];
        boolean alpha = args[3].equals("RGBA");
        int width = Integer.parseInt(args[4]);
        int height = Integer.parseInt(args[5]);
        File directory = new File(args[6]);
        directory.mkdirs();
        int[] pixels = new int[width * height];
        for (int index = 0; index < pixels.length; index++)
        {
            int r = (index * 37 + 11) % 256;
            int g = (index * 71 + 23) % 256;
            int b = (index * 97 + 31) % 256;
            int a = alpha ? (index * 53 + 61) % 256 : 255;
            pixels[index] = (a << 24) | (r << 16) | (g << 8) | b;
        }
        ImageData source = new ImageData(pixels, width, height);
        ImageData encrypted = process(algorithm, key, source, false);
        ImageData decrypted = process(algorithm, key, encrypted, true);
        save(source, new File(directory, "source.png"));
        save(encrypted, new File(directory, "java-encrypted.png"));
        save(decrypted, new File(directory, "java-restored.png"));
    }
}
