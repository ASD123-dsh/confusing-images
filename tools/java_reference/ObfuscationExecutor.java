/* Java 对照夹具：异步接口的占位依赖，验证仅调用同步算法。 */
package com.uiloalxise.async;

import java.util.concurrent.ExecutorService;

public final class ObfuscationExecutor
{
    /** 禁止夹具意外启动与像素验证无关的异步线程。 */
    public static ExecutorService getExecutor()
    {
        throw new UnsupportedOperationException("对照测试只允许同步算法");
    }
}
