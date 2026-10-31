package com.account.platform

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class BillImportTest {
    @Test fun acceptsBillBasenamesOnly() {
        for (name in listOf("支付宝账单.csv", "微信账单.XLSX", "bill.2026.CsV")) {
            assertEquals(name, BillImport.validateName(name))
        }
        for (name in listOf("", "../bill.csv", "x\\bill.csv", "bill.csv.exe", "bill.xls", "bill.csv\n", "bill\u0000.csv", "C:bill.csv", " bill.csv", "bill.csv ", "..csv")) {
            assertThrows(IllegalArgumentException::class.java) { BillImport.validateName(name) }
        }
        for (name in listOf("CON.report.csv", "LPT9.extra.xlsx", "中".repeat(66) + ".csv")) {
            assertThrows(IllegalArgumentException::class.java) { BillImport.validateName(name) }
        }
    }

    @Test fun readsOnlyTheSelectedStreamAndClosesBeforeReturning() {
        val original = byteArrayOf(0, 13, 10, -1, -128)
        var closed = false
        val result = BillImport.readSelected("原始账单.csv", null) {
            object : ByteArrayInputStream(original) {
                override fun close() { closed = true; super.close() }
            }
        }
        assertArrayEquals(original, result)
        assertTrue(closed)
    }

    @Test fun enforcesActualBoundEvenWhenProviderSizeIsUnknownOrWrong() {
        assertEquals(BillImport.MAX_BYTES, BillImport.readSelected("bill.xlsx", -1) { ByteArrayInputStream(ByteArray(BillImport.MAX_BYTES)) }.size)
        for (declared in listOf<Long?>(null, -1, 1)) {
            var closed = false
            var consumed = 0
            assertThrows(IllegalArgumentException::class.java) {
                BillImport.readSelected("bill.csv", declared) {
                    object : java.io.InputStream() {
                        override fun read(): Int { consumed++; return 1 }
                        override fun read(target: ByteArray, offset: Int, length: Int): Int {
                            java.util.Arrays.fill(target, offset, offset + length, 1.toByte())
                            consumed += length
                            return length
                        }
                        override fun close() { closed = true }
                    }
                }
            }
            assertTrue(closed)
            assertEquals(BillImport.MAX_BYTES + 1, consumed)
        }
    }

    @Test fun rejectsOversizedMetadataAndInvalidNamesBeforeOpening() {
        var opens = 0
        assertThrows(IllegalArgumentException::class.java) { BillImport.readSelected("bill.csv", BillImport.MAX_BYTES.toLong() + 1) { opens++; ByteArrayInputStream(byteArrayOf(1)) } }
        assertThrows(IllegalArgumentException::class.java) { BillImport.readSelected("bill.pdf", null) { opens++; ByteArrayInputStream(byteArrayOf(1)) } }
        assertEquals(0, opens)
    }

    @Test fun emptyUnavailableAndFailingProvidersDoNotProduceASelection() {
        assertThrows(IllegalArgumentException::class.java) { BillImport.readSelected("bill.csv", null) { ByteArrayInputStream(byteArrayOf()) } }
        assertThrows(IOException::class.java) { BillImport.readSelected("bill.csv", null) { null } }
        var closed = false
        assertThrows(IOException::class.java) {
            BillImport.readSelected("bill.xlsx", null) { object : java.io.InputStream() {
                override fun read(): Int { throw IOException("provider failure") }
                override fun close() { closed = true }
            } }
        }
        assertTrue(closed)
    }

    @Test fun selectionGateRejectsDuplicatesAndIgnoresStaleCallbacks() {
        val gate = BillImport.SelectionGate()
        val first = Any()
        val second = Any()
        assertTrue(gate.begin(first))
        assertFalse(gate.begin(second))
        assertFalse(gate.claimResult(second))
        assertTrue(gate.claimResult(first))
        assertFalse(gate.claimResult(first))
        gate.finish(first)
        assertTrue(gate.begin(second))
        gate.finish(first)
        assertFalse(gate.begin(first))
        assertFalse(gate.claimResult(first))
        assertTrue(gate.claimResult(second))
        gate.finish(second)
        assertTrue(gate.begin(first))
    }

    @Test fun providerWorkerHasBoundedBacklog() {
        val worker = BillImport.newWorker()
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        try {
            worker.execute { started.countDown(); release.await() }
            assertTrue(started.await(2, TimeUnit.SECONDS))
            worker.execute { }
            assertThrows(java.util.concurrent.RejectedExecutionException::class.java) { worker.execute { } }
        } finally {
            release.countDown()
            worker.shutdownNow()
            assertTrue(worker.awaitTermination(2, TimeUnit.SECONDS))
        }
    }
}
