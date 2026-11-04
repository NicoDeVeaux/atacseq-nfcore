nextflow.enable.dsl = 2

params.modules_config = params.modules_config ?: "${projectDir}/../../conf/modules.config"
params.output_json = params.output_json ?: 'fingerprint-labels.json'

workflow {
    def configFile = file(params.modules_config)
    def source = configFile.text
    def matcher = source =~ /(?s)withName:\s*'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT'\s*\{\s*ext\.args\s*=\s*(\{.*?\})\s*ext\.prefix/
    assert matcher.find() : 'Actual fingerprint argument closure was not found'
    def closureSource = matcher.group(1)
    def evaluateLabels = { String treatment, String control, boolean withControl ->
        def binding = new Binding([
            params: [with_control: withControl, fingerprint_bins: 100],
            meta: [id: treatment, control: control]
        ])
        def args = new GroovyShell(binding).evaluate('return ' + closureSource).call().toString()
        assert args.contains('--labels ') : 'Fingerprint command has no labels'
        def labels = args.split('--labels ', 2)[1].trim().tokenize(' ').collect { token ->
            token.replaceAll(/^['"]|['"]$/, '')
        }
        [treatment: treatment, control: control, args: args, labels: labels]
    }
    def comparisons = [
        comparison_a: evaluateLabels.call('TREATMENT_A', 'CONTROL_SHARED', true),
        comparison_b: evaluateLabels.call('TREATMENT_B', 'CONTROL_SHARED', true)
    ]
    def noControl = evaluateLabels.call('TREATMENT_A', 'CONTROL_SHARED', false)
    def namespaceCases = [
        evaluateLabels.call('CASE_A_REP1', 'CONTROL_REP1', true),
        evaluateLabels.call('CASE_A_REP1__control_CONTROL_REP1', 'CONTROL_REP1', true)
    ]
    def cleaningCase = evaluateLabels.call('CASE.mLb.clN_REP1', 'CONTROL_REP1', true)
    def emptyCleaningCases = [
        evaluateLabels.call('mLb_A_REP1', 'CONTROL_A_REP1', true),
        evaluateLabels.call('mLb_B_REP1', 'CONTROL_B_REP1', true)
    ]
    def receipt = [
        source: configFile.toString(),
        source_sha256: java.security.MessageDigest.getInstance('SHA-256').digest(source.bytes).encodeHex().toString(),
        probe_source_sha256: java.security.MessageDigest.getInstance('SHA-256').digest(file("${projectDir}/fingerprint_labels.nf").bytes).encodeHex().toString(),
        nextflow_version: nextflow.version.toString(),
        comparisons: comparisons,
        no_control: noControl,
        namespace_cases: namespaceCases,
        cleaning_case: cleaningCase,
        empty_cleaning_cases: emptyCleaningCases
    ]
    file(params.output_json).text = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(receipt)) + '\n'
    assert comparisons.values().every { comparison -> comparison.labels.size() == 2 } : 'Each comparison needs treatment and control labels'
    assert comparisons.values().every { comparison -> comparison.labels[0].startsWith(comparison.treatment) } : 'Treatment identity was lost'
    assert comparisons.values().every { comparison -> comparison.labels[1].contains(comparison.treatment) && comparison.labels[1].contains(comparison.control) } : 'Control label does not identify its comparison'
    assert comparisons.values().collectMany { comparison -> comparison.labels }.toSet().size() == 4 : 'Comparison labels collide'
    assert noControl.labels == ['TREATMENT_A.mLb.clN'] : 'No-control labeling changed'
    assert namespaceCases.collectMany { comparison -> comparison.labels }.toSet().size() == 4 : 'A legal treatment ID collides with a comparison control label'
    assert cleaningCase.labels.size() == 2 : 'Cleaning-sensitive comparison needs both labels'
    assert emptyCleaningCases.collectMany { comparison -> comparison.labels }.toSet().size() == 4 : 'Distinct controls need four raw comparison labels'
    println "Fingerprint labels: 2 comparisons, 4 distinct labels; no-control case passed"
}
