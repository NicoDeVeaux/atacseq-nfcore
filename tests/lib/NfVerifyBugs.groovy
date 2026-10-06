import groovy.json.JsonSlurper

// Helpers for the nf-verify bug pins in tests/nf-verify-bugs/ (see tests/nf-verify-bugs.md).
class NfVerifyBugs {

    // Copy a samplesheet into dir, pointing every FastQ at an empty placeholder (enough for -stub).
    static String stageSamplesheet(String csv, String dir) {
        def reads = new File(dir, 'reads')
        reads.mkdirs()
        def lines = new File(csv).readLines()
        def header = lines[0].split(',', -1) as List
        def fastqCols = header.findIndexValues { col -> col.startsWith('fastq_') }
        def out = [lines[0]]
        lines.drop(1).findAll { line -> line.trim() }.each { line ->
            def cols = line.split(',', -1) as List
            fastqCols.each { i ->
                if (cols[i]) {
                    def fq = new File(reads, cols[i].tokenize('/')[-1])
                    if (!fq.exists()) {
                        fq.bytes = [0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 3, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0] as byte[]
                    }
                    cols[i] = fq.absolutePath
                }
            }
            out << cols.join(',')
        }
        def sheet = new File(dir, 'samplesheet.csv')
        sheet.text = out.join('\n') + '\n'
        return sheet.absolutePath
    }

    // Nextflow input block for workflows/atacseq.nf:ATACSEQ with placeholder references (stub runs only).
    static String atacseqInputs(String samplesheet, String dir) {
        def ref = new File(dir, 'ref')
        new File(ref, 'bwa').mkdirs()
        [
            'genome.fa': '>chr1\nACGT\n', 'genome.fa.fai': 'chr1\t4\t6\t4\t5\n', 'genes.gtf': '', 'genes.bed': '',
            'tss.bed': '', 'genome.sizes': 'chr1\t4\n', 'genome.include_regions.bed': 'chr1\t0\t4\n',
            'autosomes.txt': 'chr1\n', 'bwa/genome.fa.bwt': ''
        ].each { name, text -> new File(ref, name).text = text }
        def r = ref.absolutePath
        return """
            input[0]  = channel.value(file('${samplesheet}'))
            input[1]  = channel.value(file('${r}/genome.fa'))
            input[2]  = channel.value(file('${r}/genome.fa.fai'))
            input[3]  = channel.value(file('${r}/genes.gtf'))
            input[4]  = channel.value(file('${r}/genes.bed'))
            input[5]  = channel.value(file('${r}/tss.bed'))
            input[6]  = channel.value(file('${r}/genome.sizes'))
            input[7]  = channel.value(file('${r}/genome.include_regions.bed'))
            input[8]  = channel.value([ [:], file('${r}/bwa') ])
            input[9]  = channel.empty()
            input[10] = channel.empty()
            input[11] = channel.empty()
            input[12] = channel.value(file('${r}/autosomes.txt'))
            input[13] = channel.value(1000)
            input[14] = null
            input[15] = null
            input[16] = null
            input[17] = '${dir}'
        """
    }

    // True if the run failed and its log says why in terms of regex (a fix that rejects the input, not an unrelated error).
    static boolean rejected(boolean success, List<String> log, String regex) {
        return !success && log.any { line -> line =~ regex }
    }

    // Tasks whose name matches regex, read from the run's work dir. meta and ext_args are recorded by
    // tests/nf-verify-bugs/nextflow.config; inputs are the staged file names.
    static List<Map> tasks(String workDir, String regex) {
        def found = []
        new File(workDir).eachDirRecurse { d ->
            def run = new File(d, '.command.run')
            if (!run.exists()) {
                return
            }
            def m = run.text =~ /(?m)^### name: '(.+)'$/
            if (!m.find() || !(m.group(1) =~ regex)) {
                return
            }
            def metaFile = new File(d, '.nf_verify_meta.json')
            def recorded = metaFile.exists() ? new JsonSlurper().parse(metaFile) : [:]
            def staged = (run.text =~ /(?m)^\s*ln -s \S+ (\S+)$/).collect { it[1] }
            found << [
                name    : m.group(1),
                meta    : recorded.meta,
                ext_args: recorded.ext_args,
                inputs  : staged.sort(),
                workDir : d
            ]
        }
        return found.sort { t -> t.name }
    }
}
